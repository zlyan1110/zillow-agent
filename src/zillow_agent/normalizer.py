"""Address normalization and fast-path routing.

The fast-path router is the key latency optimization:
if the input matches a standard US address regex, we skip
the LLM entirely and save 1-2 seconds.
"""

from __future__ import annotations

import re

from zillow_agent.models import (
    Address,
    InputClassification,
    RouterDecision,
    ZillowSearchResult,
)

# Matches: "123 Main St, City, ST 12345" or "123 Main St, City, ST"
# Flexible enough for most typed addresses, strict enough to avoid false positives.
_STANDARD_ADDRESS_RE = re.compile(
    r"^(?P<street>\d+\s+[\w\s\.#\-]+?)"  # street number + name
    r",\s*"
    r"(?P<city>[\w\s\.\-]+?)"  # city
    r",\s*"
    r"(?P<state>[A-Za-z]{2})"  # state (2-letter)
    r"(?:\s+(?P<zip>\d{5}(?:-\d{4})?))?$",  # optional ZIP
    re.UNICODE,
)

# US state codes for validation
_US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
    "DC", "PR", "VI", "GU", "AS", "MP",
}


def classify_input(query: str) -> RouterDecision:
    """Classify user input and extract address if possible.

    This is the fast-path router. Standard addresses are parsed
    directly without LLM involvement.

    Args:
        query: Raw user input string.

    Returns:
        RouterDecision with classification and optional parsed address.
    """
    cleaned = query.strip()

    match = _STANDARD_ADDRESS_RE.match(cleaned)
    if match:
        state = match.group("state").upper()
        if state in _US_STATES:
            address = Address(
                street=_normalize_street(match.group("street")),
                city=match.group("city").strip().title(),
                state=state,
                zipcode=match.group("zip"),
            )
            return RouterDecision(
                classification=InputClassification.STANDARD_ADDRESS,
                address=address,
                reason="Matched standard US address pattern",
            )

    return RouterDecision(
        classification=InputClassification.FUZZY_QUERY,
        address=None,
        reason="Does not match standard address format; requires LLM resolution",
    )


def _normalize_street(raw: str) -> str:
    """Normalize street address formatting.

    Standardizes common abbreviations and cleans whitespace.
    """
    street = raw.strip()
    street = re.sub(r"\s+", " ", street)

    # Standardize common abbreviations
    replacements = {
        r"\bSt\b": "St",
        r"\bStr\b": "St",
        r"\bAve\b": "Ave",
        r"\bBlvd\b": "Blvd",
        r"\bDr\b": "Dr",
        r"\bLn\b": "Ln",
        r"\bRd\b": "Rd",
        r"\bCt\b": "Ct",
        r"\bPl\b": "Pl",
        r"\bApt\b": "Apt",
        r"\bSte\b": "Ste",
        r"\bUnit\b": "Unit",
    }
    for pattern, replacement in replacements.items():
        street = re.sub(pattern, replacement, street, flags=re.IGNORECASE)

    return street


def build_zillow_url(address: Address) -> str:
    """Build a Zillow property detail page URL from an address.

    This constructs the URL that a human would land on when
    searching for this address on Zillow.
    """
    slug = address.to_zillow_slug()
    return f"https://www.zillow.com/homes/{slug}_rb/"


def build_zillow_detail_url(zpid: int, address: Address) -> str:
    """Build a Zillow property detail URL with ZPID.

    Format: https://www.zillow.com/homedetails/{slug}/{zpid}_zpid/
    """
    slug = address.to_zillow_slug()
    return f"https://www.zillow.com/homedetails/{slug}/{zpid}_zpid/"


def build_search_url(address: Address) -> str:
    """Build a Zillow search URL for address lookup."""
    query = address.to_search_query()
    return f"https://www.zillow.com/homes/{query.replace(' ', '-').replace(',', '')}_rb/"


# --- Street abbreviation normalization for address matching ---
_STREET_ABBREVS: dict[str, str] = {
    "street": "st", "avenue": "ave", "boulevard": "blvd", "drive": "dr",
    "lane": "ln", "road": "rd", "court": "ct", "place": "pl",
    "circle": "cir", "terrace": "ter", "way": "way", "parkway": "pkwy",
    "northwest": "nw", "northeast": "ne", "southwest": "sw", "southeast": "se",
    "north": "n", "south": "s", "east": "e", "west": "w",
}

_STREET_NUM_RE = re.compile(r"^(\d+)")


def _normalize_for_match(text: str) -> str:
    """Lowercase and normalize common street abbreviations."""
    result = text.lower().strip()
    for full, abbr in _STREET_ABBREVS.items():
        result = re.sub(rf"\b{full}\b", abbr, result)
    return result


def match_address_to_search_result(
    address: Address,
    results: list[ZillowSearchResult],
) -> ZillowSearchResult | None:
    """Find the search result that best matches the queried address.

    Scoring: street number (required, 40pts) + street name (30pts) + city (20pts) + state (10pts).
    Returns highest scorer above 70 points, or None.
    """
    query_num_match = _STREET_NUM_RE.match(address.street)
    if not query_num_match:
        return None
    query_num = query_num_match.group(1)
    query_street_norm = _normalize_for_match(address.street)
    query_city_norm = address.city.lower().strip()
    query_state_norm = address.state.lower().strip()

    best_result: ZillowSearchResult | None = None
    best_score = 0

    for result in results:
        score = 0
        result_addr = result.address or ""

        # Street number must match (required)
        result_num_match = _STREET_NUM_RE.match(result_addr.strip())
        if not result_num_match or result_num_match.group(1) != query_num:
            continue
        score += 40

        # Street name comparison
        result_addr_norm = _normalize_for_match(result_addr)
        # Extract street portion (before first comma)
        result_street_part = result_addr_norm.split(",")[0].strip()
        query_street_part = query_street_norm
        # Remove street number for comparison
        result_street_name = _STREET_NUM_RE.sub("", result_street_part).strip()
        query_street_name = _STREET_NUM_RE.sub("", query_street_part).strip()
        if result_street_name and query_street_name:
            if result_street_name == query_street_name:
                score += 30
            elif result_street_name in query_street_name or query_street_name in result_street_name:
                score += 20

        # City comparison
        result_city = (result.city or "").lower().strip()
        if not result_city:
            # Try extracting from full address string
            parts = result_addr.split(",")
            if len(parts) >= 2:
                result_city = parts[1].strip().lower()
        if result_city and result_city == query_city_norm:
            score += 20

        # State comparison
        result_state = (result.state or "").lower().strip()
        if not result_state:
            parts = result_addr.split(",")
            if len(parts) >= 3:
                state_zip = parts[2].strip().split()
                if state_zip:
                    result_state = state_zip[0].lower()
        if result_state and result_state == query_state_norm:
            score += 10

        if score > best_score:
            best_score = score
            best_result = result

    return best_result if best_score >= 70 else None
