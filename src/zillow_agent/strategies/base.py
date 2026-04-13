"""Base class for Zillow data fetch strategies.

Each strategy implements a different method of retrieving
the Zestimate from Zillow. The fetcher orchestrates them
in a fallback chain ordered by speed and reliability.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from zillow_agent.models import Address, StrategyName, ZillowSearchResult


class FetchStrategy(ABC):
    """Abstract base for Zillow data fetch strategies.

    Each implementation must:
    1. Accept an Address and return the Zestimate value + metadata.
    2. Raise specific exceptions on failure (not generic Exception).
    3. Be independently testable with mocked HTTP responses.
    """

    @property
    @abstractmethod
    def name(self) -> StrategyName:
        """Unique identifier for this strategy."""
        ...

    @abstractmethod
    async def fetch_zestimate(self, address: Address) -> dict[str, Any]:
        """Fetch the Zestimate for the given address.

        Returns:
            Dict with at minimum: zpid (int), zestimate (int).
            May also include: rent_zestimate, detail_url, raw_data.

        Raises:
            AddressNotFoundError: Address not found on Zillow.
            ZillowBlockedError: Anti-bot detection triggered.
            ParseError: Could not extract Zestimate from response.
        """
        ...

    @abstractmethod
    async def search_address(self, address: Address) -> list[ZillowSearchResult]:
        """Search Zillow for matching properties.

        Returns:
            List of matching search results, possibly empty.

        Raises:
            ZillowBlockedError: Anti-bot detection triggered.
        """
        ...

    async def close(self) -> None:  # noqa: B027
        """Clean up any resources (HTTP clients, browsers, etc)."""

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} strategy={self.name.value}>"
