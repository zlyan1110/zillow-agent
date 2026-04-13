"""Mock data for demo mode — no API keys required.

Provides fixture-based responses so reviewers can try the CLI
without setting up Scrapfly or Anthropic API keys.
"""

from __future__ import annotations

from zillow_agent.models import Address, StrategyName, ZestimateResponse

# Hardcoded fixture responses for known demo addresses
_MOCK_DATA: list[dict] = [
    {
        "street": "14933 SE 45th Pl",
        "city": "Bellevue",
        "state": "WA",
        "zipcode": "98006",
        "zestimate": 3_601_900,
        "rent_zestimate": 14_200,
        "zpid": 49009508,
    },
    {
        "street": "123 Main St",
        "city": "Seattle",
        "state": "WA",
        "zipcode": "98101",
        "zestimate": 585_000,
        "rent_zestimate": 2_800,
        "zpid": 48749425,
    },
]


def mock_lookup(query: str) -> ZestimateResponse | None:
    """Match a query against mock data and return a fixture response.

    Uses case-insensitive substring matching so both
    "14933 SE 45th Place, Bellevue, WA 98006" and
    "14933 SE 45th" will match.
    """
    query_lower = query.lower()

    for entry in _MOCK_DATA:
        # Match on street number + partial street name
        street_lower = entry["street"].lower()
        if street_lower in query_lower or query_lower in street_lower:
            address = Address(
                street=entry["street"],
                city=entry["city"],
                state=entry["state"],
                zipcode=entry["zipcode"],
            )
            return ZestimateResponse(
                address=address,
                zestimate=entry["zestimate"],
                rent_zestimate=entry["rent_zestimate"],
                zpid=entry["zpid"],
                source=StrategyName.SCRAPFLY,
                latency_ms=0.0,
                used_llm=False,
            )

    return None
