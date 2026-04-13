"""Integration tests for the full agent pipeline.

Tests the complete flow from user input through the agent
to a ZestimateResponse, using mocked fetcher throughout.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from zillow_agent.agent import ZestimateAgent
from zillow_agent.config import AgentConfig
from zillow_agent.exceptions import AllStrategiesFailedError, LLMError
from zillow_agent.models import Address, StrategyName, ZestimateRequest, ZestimateResponse


def _make_response(address: Address) -> ZestimateResponse:
    """Create a mock ZestimateResponse for testing."""
    return ZestimateResponse(
        address=address,
        zestimate=3601900,
        rent_zestimate=14200,
        zpid=49009508,
        source=StrategyName.SCRAPFLY,
        latency_ms=500.0,
    )


class TestAgentFastPath:
    """Tests for the fast-path (standard address, no LLM)."""

    @pytest.mark.asyncio
    async def test_standard_address_fast_path(self) -> None:
        """Standard address should skip LLM and succeed via Scrapfly."""
        config = AgentConfig(enable_llm=False)
        agent = ZestimateAgent(config=config)

        with patch.object(agent._fetcher, "fetch", new_callable=AsyncMock) as mock_fetch:
            expected_addr = Address(
                street="14933 SE 45th Place",
                city="Bellevue",
                state="WA",
                zipcode="98006",
            )
            mock_fetch.return_value = _make_response(expected_addr)

            try:
                request = ZestimateRequest(
                    query="14933 SE 45th Place, Bellevue, WA 98006"
                )
                result = await agent.run(request)

                assert result.zestimate == 3601900
                assert result.zpid == 49009508
                assert result.source == StrategyName.SCRAPFLY
                assert result.used_llm is False
                mock_fetch.assert_called_once()
            finally:
                await agent.close()

    @pytest.mark.asyncio
    async def test_all_strategies_fail(self) -> None:
        """When Scrapfly fails, should raise AllStrategiesFailedError."""
        config = AgentConfig(enable_llm=False)
        agent = ZestimateAgent(config=config)

        with patch.object(agent._fetcher, "fetch", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.side_effect = AllStrategiesFailedError(
                [("scrapfly", RuntimeError("blocked"))]
            )

            try:
                request = ZestimateRequest(
                    query="14933 SE 45th Place, Bellevue, WA 98006"
                )
                with pytest.raises(AllStrategiesFailedError):
                    await agent.run(request)
            finally:
                await agent.close()


class TestAgentLLMPath:
    """Tests for the LLM path (fuzzy queries)."""

    @pytest.mark.asyncio
    async def test_fuzzy_query_without_llm_raises(self) -> None:
        """Fuzzy query with LLM disabled should raise LLMError."""
        config = AgentConfig(enable_llm=False)
        agent = ZestimateAgent(config=config)

        try:
            request = ZestimateRequest(query="Nexhelm AI Location")
            with pytest.raises(LLMError, match="LLM disabled"):
                await agent.run(request)
        finally:
            await agent.close()


class TestZestimateResponse:
    """Tests for response model validation."""

    @pytest.mark.asyncio
    async def test_response_has_all_fields(self) -> None:
        """Response should contain all required metadata fields."""
        config = AgentConfig(enable_llm=False)
        agent = ZestimateAgent(config=config)

        with patch.object(agent._fetcher, "fetch", new_callable=AsyncMock) as mock_fetch:
            expected_addr = Address(
                street="14933 SE 45th Place",
                city="Bellevue",
                state="WA",
                zipcode="98006",
            )
            mock_fetch.return_value = _make_response(expected_addr)

            try:
                request = ZestimateRequest(
                    query="14933 SE 45th Place, Bellevue, WA 98006"
                )
                result = await agent.run(request)

                assert isinstance(result.zestimate, int)
                assert isinstance(result.zpid, int)
                assert result.source == StrategyName.SCRAPFLY
                assert result.fetched_at is not None
                assert result.latency_ms >= 0

                json_str = result.model_dump_json()
                assert "zestimate" in json_str
                assert "zpid" in json_str
            finally:
                await agent.close()
