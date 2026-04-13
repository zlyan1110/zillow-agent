"""Shared HTML parsing functions for Zillow property pages.

Extracts Zestimate data from Zillow's __NEXT_DATA__ JSON,
embedded JSON-LD, inline script JSON, or regex fallback.

Used by both HTMLStrategy (direct fetch) and ScrapflyStrategy
(proxy fetch) to avoid duplicating parsing logic.
"""

from __future__ import annotations

import json
import re
from typing import Any

from bs4 import BeautifulSoup

from zillow_agent.logging import get_logger

logger = get_logger(__name__)

# Regex patterns for extracting Zestimate from rendered text
_ZESTIMATE_RE = re.compile(r"\$[\d,]+(?=\s*Zestimate)", re.IGNORECASE)
_ZESTIMATE_RE2 = re.compile(r"Zestimate[^$]*\$([\d,]+)", re.IGNORECASE)
_ZESTIMATE_RE3 = re.compile(r'"zestimate"\s*:\s*(\d+)')
_ZESTIMATE_RE4 = re.compile(r"Zestimate.*?(\$[\d,]+)", re.IGNORECASE | re.DOTALL)
_ZPID_FROM_URL_RE = re.compile(r"/(\d+)_zpid")
_ZPID_JSON_RE = re.compile(r'"zpid"\s*:\s*(\d+)')


def detect_page_type(next_data: dict[str, Any]) -> str:
    """Detect whether __NEXT_DATA__ represents a search page or property detail page.

    Returns:
        "search" if searchPageState is present
        "detail" if gdpClientCache or property data is present
        "unknown" otherwise
    """
    try:
        page_props = next_data.get("props", {}).get("pageProps", {})
    except (AttributeError, TypeError):
        return "unknown"

    if page_props.get("searchPageState") is not None:
        return "search"

    comp_props = page_props.get("componentProps", {})
    if isinstance(comp_props, dict) and comp_props.get("gdpClientCache") is not None:
        return "detail"

    if page_props.get("property") is not None:
        return "detail"

    if page_props.get("aboveTheFoldData") is not None:
        return "detail"

    return "unknown"


def extract_search_results_from_next_data(
    data: dict[str, Any],
) -> list[dict[str, Any]]:
    """Extract structured search results from a search page's __NEXT_DATA__.

    Returns a list of dicts with keys: zpid, address, zestimate, street, city, state, zipcode.
    """
    results: list[dict[str, Any]] = []
    try:
        list_results = (
            data.get("props", {})
            .get("pageProps", {})
            .get("searchPageState", {})
            .get("cat1", {})
            .get("searchResults", {})
            .get("listResults", [])
        )
    except (AttributeError, TypeError):
        return results

    for item in list_results[:20]:
        info = item.get("hdpData", {}).get("homeInfo", {}) if isinstance(item, dict) else {}
        zpid = info.get("zpid") or (item.get("zpid") if isinstance(item, dict) else None)
        if not zpid:
            continue
        try:
            zpid_int = int(zpid)
        except (ValueError, TypeError):
            continue
        results.append({
            "zpid": zpid_int,
            "address": item.get("address", ""),
            "zestimate": info.get("zestimate"),
            "street": info.get("streetAddress"),
            "city": info.get("city"),
            "state": info.get("state"),
            "zipcode": info.get("zipcode"),
        })

    return results


def extract_next_data(html: str) -> dict[str, Any] | None:
    """Extract and parse __NEXT_DATA__ JSON from HTML."""
    soup = BeautifulSoup(html, "lxml")
    script = soup.find("script", {"id": "__NEXT_DATA__"})
    if not script or not script.string:
        return None

    try:
        return json.loads(script.string)
    except json.JSONDecodeError:
        logger.warning("next_data_json_decode_failed")
        return None


def _find_zestimate_in_obj(obj: Any, depth: int = 0) -> dict[str, Any] | None:
    """Recursively search a nested dict/list for zestimate data."""
    if depth > 8 or obj is None:
        return None

    if isinstance(obj, dict):
        # Check if this dict directly has zestimate
        zestimate = obj.get("zestimate")
        if zestimate and isinstance(zestimate, (int, float)) and zestimate > 10000:
            zpid = obj.get("zpid", 0)
            return {
                "zpid": int(zpid) if zpid else 0,
                "zestimate": int(zestimate),
                "rent_zestimate": obj.get("rentZestimate"),
                "raw_data": obj,
            }

        # Check for property sub-key
        prop = obj.get("property")
        if isinstance(prop, dict):
            result = _find_zestimate_in_obj(prop, depth + 1)
            if result:
                return result

        # Recurse into known structural keys first
        for key in ("data", "componentProps", "gdpClientCache", "initialData",
                    "pageProps", "props", "building", "homeInfo", "propertyData",
                    "aboveTheFoldData", "listingDataByZpid"):
            if key in obj:
                val = obj[key]
                if isinstance(val, str):
                    try:
                        val = json.loads(val)
                    except (json.JSONDecodeError, TypeError):
                        continue
                result = _find_zestimate_in_obj(val, depth + 1)
                if result:
                    return result

        # For any remaining dict values: try parsing strings as JSON and recurse
        for _key, value in obj.items():
            if isinstance(value, str) and len(value) > 20 and "{" in value:
                try:
                    value = json.loads(value)
                except (json.JSONDecodeError, TypeError):
                    continue
                result = _find_zestimate_in_obj(value, depth + 1)
                if result:
                    return result
            elif isinstance(value, dict):
                result = _find_zestimate_in_obj(value, depth + 1)
                if result:
                    return result

    elif isinstance(obj, list):
        for item in obj[:10]:
            result = _find_zestimate_in_obj(item, depth + 1)
            if result:
                return result

    return None


def extract_zestimate_from_next_data(
    data: dict[str, Any],
) -> dict[str, Any] | None:
    """Navigate the __NEXT_DATA__ structure to find Zestimate.

    The structure varies between Zillow builds. We try specific
    known paths first, then fall back to recursive search.
    """
    # Known paths where property data lives in __NEXT_DATA__
    paths_to_try = [
        # Path 1: gdpClientCache (common 2024-2026)
        lambda d: d["props"]["pageProps"]["componentProps"]["gdpClientCache"],
        # Path 2: direct property
        lambda d: d["props"]["pageProps"]["property"],
        # Path 3: initialData
        lambda d: d["props"]["pageProps"]["initialData"],
        # Path 4: aboveTheFoldData (newer builds)
        lambda d: d["props"]["pageProps"]["aboveTheFoldData"],
        # Path 5: componentProps directly
        lambda d: d["props"]["pageProps"]["componentProps"],
        # Path 6: top-level query data
        lambda d: d["props"]["pageProps"],
    ]

    for path_fn in paths_to_try:
        try:
            result = path_fn(data)
            if isinstance(result, str):
                result = json.loads(result)

            found = _find_zestimate_in_obj(result)
            if found:
                return found

        except (KeyError, TypeError, IndexError, json.JSONDecodeError):
            continue

    # Last resort: recursive search from root
    return _find_zestimate_in_obj(data)


def extract_zestimate_from_script_tags(html: str) -> dict[str, Any] | None:
    """Search all script tags for JSON containing zestimate data."""
    soup = BeautifulSoup(html, "lxml")

    # Try JSON-LD first
    for script in soup.find_all("script", {"type": "application/ld+json"}):
        if not script.string:
            continue
        try:
            ld_data = json.loads(script.string)
            if isinstance(ld_data, list):
                for item in ld_data:
                    if isinstance(item, dict) and item.get("@type") == "SingleFamilyResidence":
                        # JSON-LD doesn't usually have zestimate but may have price
                        pass
            elif isinstance(ld_data, dict) and ld_data.get("@type") == "SingleFamilyResidence":
                pass
        except (json.JSONDecodeError, TypeError):
            continue

    # Search all inline scripts for JSON objects with zestimate
    for script in soup.find_all("script"):
        if script.get("id") == "__NEXT_DATA__":
            continue  # Already handled
        if not script.string:
            continue
        text = script.string

        # Look for JSON objects containing zestimate
        # Pattern: {"zpid":123,"zestimate":456789,...}
        json_pattern = re.compile(r'\{[^{}]*"zestimate"\s*:\s*\d+[^{}]*\}')
        for match in json_pattern.finditer(text):
            try:
                obj = json.loads(match.group())
                zest = obj.get("zestimate")
                if zest and isinstance(zest, (int, float)) and zest > 10000:
                    return {
                        "zpid": int(obj.get("zpid", 0)),
                        "zestimate": int(zest),
                        "rent_zestimate": obj.get("rentZestimate"),
                        "raw_data": obj,
                    }
            except (json.JSONDecodeError, TypeError):
                continue

        # Try to find larger JSON blobs
        if '"zestimate"' in text:
            # Try to extract JSON starting from common patterns
            for start_pattern in [
                re.compile(r'(\{["\']zpid["\'].*?\})\s*[,;\]]'),
                re.compile(r'JSON\.parse\(["\'](.+?)["\']\)'),
            ]:
                for m in start_pattern.finditer(text):
                    try:
                        snippet = m.group(1).replace("\\'", "'").replace('\\"', '"')
                        obj = json.loads(snippet)
                        result = _find_zestimate_in_obj(obj)
                        if result:
                            return result
                    except (json.JSONDecodeError, TypeError):
                        continue

    return None


def extract_zestimate_from_text(html: str) -> dict[str, Any] | None:
    """Regex fallback: extract Zestimate from rendered page text."""
    # Try multiple patterns
    zestimate = None

    # Pattern 1: "$X,XXX,XXX Zestimate" (value before label)
    match = _ZESTIMATE_RE.search(html)
    if match:
        value_str = match.group().replace("$", "").replace(",", "")
        try:
            val = int(value_str)
            if val > 10000:
                zestimate = val
        except ValueError:
            pass

    # Pattern 2: "Zestimate ... $X,XXX,XXX" (label before value)
    if not zestimate:
        match = _ZESTIMATE_RE2.search(html)
        if match:
            value_str = match.group(1).replace(",", "")
            try:
                val = int(value_str)
                if val > 10000:
                    zestimate = val
            except ValueError:
                pass

    # Pattern 3: "zestimate": 123456 (JSON in page source)
    if not zestimate:
        match = _ZESTIMATE_RE3.search(html)
        if match:
            try:
                val = int(match.group(1))
                if val > 10000:  # Filter out non-price values
                    zestimate = val
            except ValueError:
                pass

    # Pattern 4: Zestimate ... $X,XXX (with anything in between, dotall)
    if not zestimate:
        match = _ZESTIMATE_RE4.search(html[:50000])
        if match:
            value_str = match.group(1).replace("$", "").replace(",", "")
            try:
                val = int(value_str)
                if val > 10000:
                    zestimate = val
            except ValueError:
                pass

    if not zestimate:
        return None

    # Try to extract ZPID
    zpid = 0
    zpid_match = _ZPID_FROM_URL_RE.search(html)
    if zpid_match:
        zpid = int(zpid_match.group(1))
    else:
        zpid_match = _ZPID_JSON_RE.search(html)
        if zpid_match:
            zpid = int(zpid_match.group(1))

    return {
        "zpid": zpid,
        "zestimate": zestimate,
        "rent_zestimate": None,
        "raw_data": None,
    }
