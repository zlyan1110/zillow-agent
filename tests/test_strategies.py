"""Tests for shared parsing logic used by Scrapfly strategy.

Validates parsing functions against fixture data so that
if Zillow changes their page structure, these tests catch it.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from zillow_agent.models import Address, ZillowSearchResult
from zillow_agent.normalizer import build_zillow_detail_url, match_address_to_search_result
from zillow_agent.parsers import (
    detect_page_type,
    extract_next_data,
    extract_search_results_from_next_data,
    extract_zestimate_from_next_data,
    extract_zestimate_from_text,
)


class TestParsers:
    """Tests for the shared HTML/JSON parsing functions."""

    def test_extract_next_data(self, property_page_html: str) -> None:
        """Should extract __NEXT_DATA__ JSON from HTML."""
        data = extract_next_data(property_page_html)
        assert data is not None
        assert "props" in data
        assert "pageProps" in data["props"]

    def test_extract_zestimate_from_next_data(self, next_data_json: dict) -> None:
        """Should navigate __NEXT_DATA__ structure to find Zestimate."""
        result = extract_zestimate_from_next_data(next_data_json)
        assert result is not None
        assert result["zpid"] == 49009508
        assert result["zestimate"] == 3601900
        assert result["rent_zestimate"] == 14200

    def test_extract_zestimate_from_text(self, property_page_html: str) -> None:
        """Regex fallback should find Zestimate in page text."""
        result = extract_zestimate_from_text(property_page_html)
        assert result is not None
        assert result["zestimate"] == 3601900

    def test_extract_from_empty_html(self) -> None:
        """Empty HTML should return None, not crash."""
        result = extract_next_data("<html><body></body></html>")
        assert result is None

    def test_extract_from_html_no_zestimate(self) -> None:
        """HTML without Zestimate text should return None from regex."""
        html = "<html><body>No property data here</body></html>"
        result = extract_zestimate_from_text(html)
        assert result is None


class TestPageTypeDetection:
    """Tests for detect_page_type()."""

    def test_detect_search_page(self, next_data_search_json: dict) -> None:
        assert detect_page_type(next_data_search_json) == "search"

    def test_detect_detail_page(self, next_data_json: dict) -> None:
        assert detect_page_type(next_data_json) == "detail"

    def test_detect_unknown_page(self) -> None:
        assert detect_page_type({"props": {"pageProps": {}}}) == "unknown"

    def test_detect_empty_data(self) -> None:
        assert detect_page_type({}) == "unknown"


class TestSearchResultExtraction:
    """Tests for extract_search_results_from_next_data()."""

    def test_extract_search_results(self, next_data_search_json: dict) -> None:
        results = extract_search_results_from_next_data(next_data_search_json)
        assert len(results) == 3
        assert results[0]["zpid"] == 49009508
        assert results[0]["zestimate"] == 3601900
        assert results[0]["street"] == "14933 SE 45th Pl"

    def test_extract_from_detail_page(self, next_data_json: dict) -> None:
        """Detail page has no searchPageState, should return empty list."""
        results = extract_search_results_from_next_data(next_data_json)
        assert results == []

    def test_extract_from_empty(self) -> None:
        results = extract_search_results_from_next_data({})
        assert results == []


class TestMinimumZestimateFilter:
    """Tests for the $10k minimum filter in regex parsing."""

    def test_regex_rejects_tiny_zestimate(self) -> None:
        html = "<div>$1,923 Zestimate</div>"
        result = extract_zestimate_from_text(html)
        assert result is None

    def test_regex_rejects_small_zestimate(self) -> None:
        html = "<div>Zestimate: $5,124</div>"
        result = extract_zestimate_from_text(html)
        assert result is None

    def test_regex_accepts_real_zestimate(self) -> None:
        html = '<div>$3,601,900 Zestimate</div><a href="/49009508_zpid">'
        result = extract_zestimate_from_text(html)
        assert result is not None
        assert result["zestimate"] == 3601900


class TestAddressMatching:
    """Tests for match_address_to_search_result()."""

    def test_exact_match(self, bellevue_address: Address) -> None:
        results = [
            ZillowSearchResult(
                zpid=49009508,
                address="14933 SE 45th Pl, Bellevue, WA 98006",
                zestimate=3601900,
            ),
            ZillowSearchResult(
                zpid=49009510,
                address="14935 SE 45th Pl, Bellevue, WA 98006",
                zestimate=2850000,
            ),
        ]
        match = match_address_to_search_result(bellevue_address, results)
        assert match is not None
        assert match.zpid == 49009508

    def test_no_match(self, bellevue_address: Address) -> None:
        results = [
            ZillowSearchResult(
                zpid=999,
                address="500 Totally Different St, Chicago, IL 60601",
                zestimate=300000,
            ),
        ]
        match = match_address_to_search_result(bellevue_address, results)
        assert match is None

    def test_empty_results(self, bellevue_address: Address) -> None:
        match = match_address_to_search_result(bellevue_address, [])
        assert match is None

    def test_matches_by_street_number(self) -> None:
        """Should match on street number + name even with slightly different formatting."""
        addr = Address(street="123 Main St", city="Seattle", state="WA", zipcode="98101")
        results = [
            ZillowSearchResult(
                zpid=111,
                address="125 Main St, Seattle, WA 98101",
                zestimate=500000,
            ),
            ZillowSearchResult(
                zpid=222,
                address="123 Main St, Seattle, WA 98101",
                zestimate=600000,
            ),
        ]
        match = match_address_to_search_result(addr, results)
        assert match is not None
        assert match.zpid == 222


class TestBuildZillowDetailUrl:
    """Tests for build_zillow_detail_url()."""

    def test_detail_url_format(self, bellevue_address: Address) -> None:
        url = build_zillow_detail_url(49009508, bellevue_address)
        assert url.startswith("https://www.zillow.com/homedetails/")
        assert "49009508_zpid" in url
        assert " " not in url

    def test_detail_url_contains_slug(self, bellevue_address: Address) -> None:
        url = build_zillow_detail_url(49009508, bellevue_address)
        assert "Bellevue" in url


class TestHybridFetchFlow:
    """Integration test for the hybrid search→detail fetch flow."""

    @pytest.mark.asyncio
    async def test_search_page_returns_correct_property(
        self, next_data_search_json: dict, bellevue_address: Address
    ) -> None:
        """When Scrapfly returns a search page, hybrid logic should
        match the correct property and return its zestimate."""
        import json

        from zillow_agent.config import ScrapflyConfig
        from zillow_agent.strategies.scrapfly_strategy import ScrapflyStrategy

        # Build HTML with the search __NEXT_DATA__
        html = (
            '<html><head><script id="__NEXT_DATA__" type="application/json">'
            + json.dumps(next_data_search_json)
            + "</script></head><body></body></html>"
        )

        config = ScrapflyConfig(api_key="test", render_js=False)
        strategy = ScrapflyStrategy(config=config)

        try:
            with patch.object(
                strategy, "_fetch_via_scrapfly", new_callable=AsyncMock
            ) as mock_fetch:
                mock_fetch.return_value = html
                result = await strategy.fetch_zestimate(bellevue_address)

                assert result["zpid"] == 49009508
                assert result["zestimate"] == 3601900
                # Should only need 1 Scrapfly call (zestimate in search results)
                assert mock_fetch.call_count == 1
        finally:
            await strategy.close()
