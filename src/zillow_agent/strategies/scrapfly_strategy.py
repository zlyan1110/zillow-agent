"""Scrapfly strategy - uses Scrapfly's anti-bot proxy to fetch Zillow pages.

Scrapfly's ASP (Anti-Scraping Protection) handles PerimeterX bypass
automatically via residential proxies + real browser clusters.
We send a Zillow URL, get back rendered HTML, then parse it.

Typical latency: 2-10s. Requires a SCRAPFLY_API_KEY env var.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from zillow_agent.config import ScrapflyConfig
from zillow_agent.exceptions import (
    AddressNotFoundError,
    NoZestimateError,
    ParseError,
    ZillowBlockedError,
)
from zillow_agent.logging import get_logger
from zillow_agent.models import Address, StrategyName, ZillowSearchResult
from zillow_agent.normalizer import (
    build_zillow_detail_url,
    build_zillow_url,
    match_address_to_search_result,
)
from zillow_agent.parsers import (
    detect_page_type,
    extract_next_data,
    extract_search_results_from_next_data,
    extract_zestimate_from_next_data,
    extract_zestimate_from_script_tags,
    extract_zestimate_from_text,
)
from zillow_agent.strategies.base import FetchStrategy

logger = get_logger(__name__)


class ScrapflyStrategy(FetchStrategy):
    """Fetch Zestimate via Scrapfly anti-bot proxy.

    Sends a Zillow property page URL to Scrapfly's scrape API
    with asp=True (Anti-Scraping Protection). Scrapfly returns
    the fully rendered HTML, which we parse using shared parsers.

    Set SCRAPFLY_API_KEY env var or pass config with api_key.
    """

    def __init__(
        self,
        config: ScrapflyConfig | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._config = config or ScrapflyConfig()
        self._api_key = self._config.api_key or os.environ.get("SCRAPFLY_API_KEY", "")
        self._client = client
        self._owns_client = client is None

    @property
    def name(self) -> StrategyName:
        return StrategyName.SCRAPFLY

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(12.0),
                follow_redirects=True,
            )
        return self._client

    async def _fetch_via_scrapfly(self, url: str, render_js: bool | None = None) -> str:
        """Fetch a URL through Scrapfly and return the HTML content.

        Args:
            url: The URL to fetch.
            render_js: Override JS rendering. None uses config default.
        """
        if not self._api_key:
            raise RuntimeError(
                "SCRAPFLY_API_KEY not set. Get a key at https://scrapfly.io"
            )

        client = await self._get_client()
        use_render_js = render_js if render_js is not None else self._config.render_js
        params: dict[str, str] = {
            "key": self._api_key,
            "url": url,
            "asp": str(self._config.asp).lower(),
            "render_js": str(use_render_js).lower(),
            "country": self._config.country,
        }

        try:
            resp = await client.get(self._config.base_url, params=params)
        except httpx.HTTPError as e:
            raise ZillowBlockedError(
                f"Scrapfly HTTP error: {e}", strategy=self.name.value
            ) from e

        if resp.status_code == 401:
            raise ZillowBlockedError(
                "Scrapfly API key invalid. Check your SCRAPFLY_API_KEY.",
                strategy=self.name.value,
            )

        if resp.status_code == 429:
            raise ZillowBlockedError(
                "Scrapfly rate limit exceeded", strategy=self.name.value
            )

        if resp.status_code != 200:
            raise ZillowBlockedError(
                f"Scrapfly returned status {resp.status_code}: {resp.text[:200]}",
                strategy=self.name.value,
            )

        try:
            data = resp.json()
        except Exception as e:
            raise ParseError(
                "Failed to decode Scrapfly response", strategy=self.name.value
            ) from e

        result = data.get("result", {})

        # Check upstream status (Zillow's response through Scrapfly)
        upstream_status = result.get("status_code", 200)
        if upstream_status == 404:
            raise AddressNotFoundError(
                f"Zillow returned 404 via Scrapfly for {url}",
                strategy=self.name.value,
            )
        if upstream_status in (403, 429):
            raise ZillowBlockedError(
                f"Zillow blocked via Scrapfly with status {upstream_status}",
                strategy=self.name.value,
            )

        content = result.get("content", "")
        if not content:
            raise ParseError(
                "Scrapfly returned empty content", strategy=self.name.value
            )

        return content

    async def search_address(self, address: Address) -> list[ZillowSearchResult]:
        """Search for properties by fetching the Zillow search page via Scrapfly."""
        url = build_zillow_url(address)

        try:
            html = await self._fetch_via_scrapfly(url)
        except (ZillowBlockedError, AddressNotFoundError):
            return []

        data = extract_next_data(html)
        if not data:
            return []

        return [
            ZillowSearchResult(
                zpid=item["zpid"],
                address=item.get("address", ""),
                zestimate=item.get("zestimate"),
                street=item.get("street"),
                city=item.get("city"),
                state=item.get("state"),
                zipcode=item.get("zipcode"),
            )
            for item in extract_search_results_from_next_data(data)
        ]

    def _try_parse_html(self, html: str) -> dict[str, Any] | None:
        """Try all parsing methods on HTML content."""
        # Try __NEXT_DATA__ first (most reliable)
        next_data = extract_next_data(html)
        if next_data:
            result = extract_zestimate_from_next_data(next_data)
            if result:
                return result

        # Try other script tags (JSON-LD, inline JSON)
        result = extract_zestimate_from_script_tags(html)
        if result:
            return result

        # Fallback to regex on page text
        return extract_zestimate_from_text(html)

    async def _handle_search_results(
        self,
        next_data: dict[str, Any],
        address: Address,
    ) -> dict[str, Any]:
        """Handle a search results page: match address, extract or fetch zestimate."""
        result_dicts = extract_search_results_from_next_data(next_data)
        search_results = [
            ZillowSearchResult(
                zpid=item["zpid"],
                address=item.get("address", ""),
                zestimate=item.get("zestimate"),
                street=item.get("street"),
                city=item.get("city"),
                state=item.get("state"),
                zipcode=item.get("zipcode"),
            )
            for item in result_dicts
        ]

        if not search_results:
            raise AddressNotFoundError(
                f"No search results for {address.to_search_query()}",
                strategy=self.name.value,
            )

        match = match_address_to_search_result(address, search_results)
        if match is None:
            logger.warning(
                "no_address_match",
                queried=address.to_search_query(),
                candidates=[r.address for r in search_results[:5]],
            )
            raise AddressNotFoundError(
                f"No matching property found for {address.to_search_query()}",
                strategy=self.name.value,
            )

        logger.info(
            "search_result_matched",
            zpid=match.zpid,
            matched_address=match.address,
            zestimate=match.zestimate,
        )

        # If search result already has a zestimate, return it (single fetch)
        if match.zestimate and match.zestimate > 10000:
            return {
                "zpid": match.zpid,
                "zestimate": match.zestimate,
                "rent_zestimate": None,
                "raw_data": None,
            }

        # Otherwise fetch the property detail page using the ZPID
        detail_url = build_zillow_detail_url(match.zpid, address)
        logger.info("fetching_detail_page", url=detail_url, zpid=match.zpid)

        detail_html = await self._fetch_via_scrapfly(detail_url)
        result = self._try_parse_html(detail_html)
        if result:
            result["zpid"] = match.zpid
            return result

        raise ParseError(
            f"Found property {match.zpid} but could not extract Zestimate from detail page",
            strategy=self.name.value,
        )

    async def fetch_zestimate(self, address: Address) -> dict[str, Any]:
        """Fetch Zestimate via Scrapfly using hybrid search→detail approach.

        1. Fetch the search URL
        2. Detect page type from __NEXT_DATA__
           - detail page: parse normally
           - search page: match address, extract zestimate or fetch detail page
           - unknown: fallback parsing + render_js retry
        """
        url = build_zillow_url(address)
        logger.info("scrapfly_fetch_start", strategy=self.name.value, url=url)

        html = await self._fetch_via_scrapfly(url)
        logger.info(
            "scrapfly_html_received",
            length=len(html),
            has_next_data="__NEXT_DATA__" in html,
            render_js=self._config.render_js,
        )

        # Try __NEXT_DATA__ with page type detection
        next_data = extract_next_data(html)
        if next_data:
            page_type = detect_page_type(next_data)
            logger.info("page_type_detected", page_type=page_type)

            if page_type == "detail":
                result = extract_zestimate_from_next_data(next_data)
                if result:
                    logger.info("scrapfly_fetch_success", strategy=self.name.value,
                                zestimate=result["zestimate"], source="detail_page")
                    return result
                # Detail page found but no Zestimate — property exists but Zillow
                # doesn't have enough data to generate an estimate
                raise NoZestimateError(
                    f"Property found on Zillow but no Zestimate is available for "
                    f"{address.to_search_query()}. Not all properties have Zestimates.",
                    strategy=self.name.value,
                )

            elif page_type == "search":
                return await self._handle_search_results(next_data, address)

        # Fallback: try all parsing methods on current HTML
        result = self._try_parse_html(html)
        if result and result["zestimate"] > 10000:
            logger.info("scrapfly_fetch_success", strategy=self.name.value,
                        zestimate=result["zestimate"], source="fallback_parse")
            return result

        # Last resort: retry with opposite render_js
        alt_render_js = not self._config.render_js
        logger.info("scrapfly_retry_alt_render", render_js=alt_render_js)

        try:
            html2 = await self._fetch_via_scrapfly(url, render_js=alt_render_js)
        except (ZillowBlockedError, ParseError) as e:
            logger.warning("scrapfly_alt_render_failed", error=str(e))
            html2 = ""

        if html2:
            result = self._try_parse_html(html2)
            if result and result["zestimate"] > 10000:
                logger.info("scrapfly_fetch_success", strategy=self.name.value,
                            zestimate=result["zestimate"], source="alt_render")
                return result

        raise ParseError(
            f"Could not extract Zestimate for {address.to_search_query()}. "
            "Zillow may have changed their page structure, or this property "
            "may not have a Zestimate.",
            strategy=self.name.value,
        )

    async def close(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None
