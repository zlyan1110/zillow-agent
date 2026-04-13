"""Fetch strategies for Zillow data retrieval."""

from zillow_agent.strategies.base import FetchStrategy
from zillow_agent.strategies.scrapfly_strategy import ScrapflyStrategy

__all__ = [
    "FetchStrategy",
    "ScrapflyStrategy",
]
