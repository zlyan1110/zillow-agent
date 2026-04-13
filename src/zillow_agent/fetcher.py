"""Zestimate fetcher using Scrapfly strategy.

Uses Scrapfly's anti-bot proxy with ASP bypass for reliable
Zillow data retrieval. This is the core of the deterministic
pipeline - no LLM involvement at this layer.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from zillow_agent.config import AgentConfig
from zillow_agent.exceptions import (
    AllStrategiesFailedError,
    ZillowBlockedError,
)
from zillow_agent.logging import get_logger
from zillow_agent.models import Address, ZestimateResponse
from zillow_agent.strategies.scrapfly_strategy import ScrapflyStrategy

logger = get_logger(__name__)


class ZestimateFetcher:
    """Fetches Zestimates via Scrapfly.

    Scrapfly handles PerimeterX bypass with ~99% success rate.
    Requires SCRAPFLY_API_KEY environment variable or config.
    """

    def __init__(self, config: AgentConfig | None = None) -> None:
        self._config = config or AgentConfig()
        self._strategy: ScrapflyStrategy | None = None
        self._initialized = False

    def _init_strategy(self) -> None:
        """Lazy-initialize the Scrapfly strategy."""
        if self._initialized:
            return

        api_key = self._config.scrapfly.api_key or os.environ.get("SCRAPFLY_API_KEY")
        if not api_key:
            raise RuntimeError(
                "SCRAPFLY_API_KEY environment variable not set. "
                "Get a key at https://scrapfly.io/"
            )

        self._strategy = ScrapflyStrategy(config=self._config.scrapfly)
        self._initialized = True

    async def fetch(self, address: Address) -> ZestimateResponse:
        """Fetch the Zestimate for a property address.

        Args:
            address: Normalized property address.

        Returns:
            ZestimateResponse with the Zestimate and metadata.

        Raises:
            AllStrategiesFailedError: Scrapfly fetch failed after retries.
        """
        self._init_strategy()
        assert self._strategy is not None

        start_time = time.monotonic()
        timeout = self._config.timeout.scrapfly_timeout

        logger.info(
            "strategy_attempt",
            strategy="scrapfly",
            address=address.to_search_query(),
            timeout=timeout,
        )

        try:
            result = await asyncio.wait_for(
                self._fetch_with_retry(address),
                timeout=timeout,
            )

            latency_ms = (time.monotonic() - start_time) * 1000

            response = ZestimateResponse(
                address=address,
                zestimate=result["zestimate"],
                rent_zestimate=result.get("rent_zestimate"),
                zpid=result["zpid"],
                source=self._strategy.name,
                latency_ms=round(latency_ms, 1),
            )

            logger.info(
                "fetch_success",
                strategy="scrapfly",
                zpid=result["zpid"],
                zestimate=result["zestimate"],
                latency_ms=round(latency_ms, 1),
            )

            return response

        except TimeoutError as e:
            elapsed = (time.monotonic() - start_time) * 1000
            err = TimeoutError(f"scrapfly timed out after {elapsed:.0f}ms")
            logger.warning("strategy_timeout", strategy="scrapfly", elapsed_ms=round(elapsed, 1))
            raise AllStrategiesFailedError([("scrapfly", err)]) from e

        except Exception as e:
            logger.warning(
                "strategy_failed",
                strategy="scrapfly",
                error_type=type(e).__name__,
                error=str(e),
            )
            raise AllStrategiesFailedError([("scrapfly", e)]) from e

    @retry(
        retry=retry_if_exception_type(ZillowBlockedError),
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=1, max=3),
        reraise=True,
    )
    async def _fetch_with_retry(self, address: Address) -> dict[str, Any]:
        """Fetch with retry on transient errors (blocking/rate limiting)."""
        assert self._strategy is not None
        return await self._strategy.fetch_zestimate(address)

    async def close(self) -> None:
        """Clean up strategy resources."""
        if self._strategy:
            try:
                await self._strategy.close()
            except Exception as e:
                logger.warning("strategy_close_error", strategy="scrapfly", error=str(e))
