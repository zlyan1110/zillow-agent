"""LLM Agent layer - Claude tool use for address understanding.

This is the "brain" of the agent. It handles:
1. Fuzzy address resolution ("Nexhelm AI Location" -> structured address)
2. Multi-result disambiguation
3. Error explanation in natural language

The LLM sees exactly 3 tools. It does NOT see low-level
HTTP/parsing details - those are encapsulated in the fetcher.

The fast-path router in normalizer.py bypasses this layer
entirely for standard address formats (~80% of queries).
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from typing import Any

from zillow_agent.config import AgentConfig
from zillow_agent.exceptions import (
    LLMError,
    ZillowAgentError,
)
from zillow_agent.fetcher import ZestimateFetcher
from zillow_agent.logging import get_logger
from zillow_agent.models import (
    Address,
    InputClassification,
    ZestimateRequest,
    ZestimateResponse,
)
from zillow_agent.normalizer import classify_input

logger = get_logger(__name__)

# Tool definitions for Claude
_TOOLS = [
    {
        "name": "search_web",
        "description": (
            "Search the web for information. Use this when you need to look up "
            "the address of a company, landmark, or place you don't know. "
            "Search for something like 'CompanyName office address location' "
            "to find the street address."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query to find the information needed",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "resolve_address",
        "description": (
            "Resolve a fuzzy or natural language location query into a structured "
            "US property address. Use this AFTER you know the actual street address "
            "(from your knowledge or from search_web results). "
            "Do NOT guess or fabricate addresses — if unsure, use search_web first."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "street": {
                    "type": "string",
                    "description": "Street address including number and street name",
                },
                "city": {
                    "type": "string",
                    "description": "City name",
                },
                "state": {
                    "type": "string",
                    "description": "Two-letter US state code (e.g. CA, NY, WA)",
                },
                "zipcode": {
                    "type": "string",
                    "description": "5-digit ZIP code if known, otherwise omit",
                },
            },
            "required": ["street", "city", "state"],
        },
    },
    {
        "name": "fetch_zestimate",
        "description": (
            "Fetch the current Zillow Zestimate for a property given its "
            "structured address. Returns the estimated value in USD."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "street": {"type": "string"},
                "city": {"type": "string"},
                "state": {"type": "string"},
                "zipcode": {"type": "string"},
            },
            "required": ["street", "city", "state"],
        },
    },
    {
        "name": "report_result",
        "description": (
            "Report the final Zestimate result or explain why the lookup failed. "
            "Always call this as the final step."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "success": {"type": "boolean"},
                "zestimate": {
                    "type": "integer",
                    "description": "Zestimate value in USD (if successful)",
                },
                "explanation": {
                    "type": "string",
                    "description": "Brief explanation of the result or failure reason",
                },
            },
            "required": ["success", "explanation"],
        },
    },
]

_SYSTEM_PROMPT = """You are a Zillow Zestimate lookup agent. Your job is to find the
current Zestimate (estimated home value) for a US property address.

Workflow:
1. If the user gives a company name, landmark, or place you don't know the
   exact address of, use search_web FIRST to find the street address.
2. Once you have a specific street address, use resolve_address to structure it.
3. Call fetch_zestimate with the structured address.
4. Call report_result with the outcome.

Rules:
- NEVER guess or fabricate an address. If you don't know the exact street
  address, use search_web to look it up.
- Always resolve to the most specific street address possible.
- If the query is ambiguous (e.g. "123 Main St" without city/state),
  make your best guess based on context, or pick the most common match.
- Never fabricate a Zestimate value - only report what fetch_zestimate returns.
- If fetch_zestimate fails, explain why in report_result.
"""


class ZestimateAgent:
    """Top-level agent that combines LLM reasoning with deterministic fetching.

    The agent has two paths:
    1. Fast path: standard address -> skip LLM -> fetch directly
    2. LLM path: fuzzy input -> Claude resolves -> fetch

    Both paths converge at the same ZestimateFetcher for data retrieval.
    """

    def __init__(self, config: AgentConfig | None = None) -> None:
        self._config = config or AgentConfig()
        self._fetcher = ZestimateFetcher(config=self._config)
        self._anthropic_client = None

    def _get_anthropic_client(self) -> Any:
        """Lazy-initialize the Anthropic client."""
        if self._anthropic_client is not None:
            return self._anthropic_client

        try:
            import anthropic
        except ImportError as e:
            raise LLMError(
                "anthropic package not installed. Run: pip install anthropic"
            ) from e

        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise LLMError(
                "ANTHROPIC_API_KEY environment variable not set"
            )

        self._anthropic_client = anthropic.Anthropic(api_key=api_key)
        return self._anthropic_client

    async def run(self, request: ZestimateRequest) -> ZestimateResponse:
        """Execute the agent pipeline.

        1. Classify input (fast-path router)
        2. If standard address: skip LLM, fetch directly
        3. If fuzzy query: use LLM to resolve, then fetch
        """
        start_time = time.monotonic()

        # Step 1: Fast-path classification
        decision = classify_input(request.query)
        logger.info(
            "input_classified",
            classification=decision.classification.value,
            reason=decision.reason,
        )

        # Step 2: Route based on classification
        if (
            decision.classification == InputClassification.STANDARD_ADDRESS
            and decision.address is not None
        ):
            # Fast path - skip LLM entirely
            logger.info("fast_path", address=decision.address.to_search_query())
            response = await self._fetcher.fetch(decision.address)
            response.used_llm = False
            return response

        # Step 3: LLM path for fuzzy queries
        if not self._config.enable_llm:
            raise LLMError(
                f"LLM disabled but input requires LLM resolution: {request.query}"
            )

        address = await self._resolve_with_llm(request.query)
        response = await self._fetcher.fetch(address)
        response.used_llm = True
        response.latency_ms = round((time.monotonic() - start_time) * 1000, 1)
        return response

    async def _resolve_with_llm(self, query: str) -> Address:
        """Use Claude to resolve a fuzzy query to a structured address."""
        client = self._get_anthropic_client()

        messages: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": (
                    f"Find the Zestimate for: {query}\n\n"
                    "First resolve this to a specific US street address, "
                    "then fetch the Zestimate."
                ),
            }
        ]

        logger.info("llm_resolve_start", query=query)

        # ReAct loop - let the LLM call tools until it reports a result
        max_turns = 8
        resolved_address: Address | None = None

        for turn in range(max_turns):
            try:
                response = client.messages.create(
                    model=self._config.llm.model,
                    max_tokens=self._config.llm.max_tokens,
                    temperature=self._config.llm.temperature,
                    system=_SYSTEM_PROMPT,
                    tools=_TOOLS,
                    messages=messages,
                )
            except Exception as e:
                raise LLMError(f"Claude API error: {e}") from e

            # Process response content blocks
            assistant_content = response.content
            messages.append({"role": "assistant", "content": assistant_content})

            # Check if the model wants to use a tool
            if response.stop_reason == "tool_use":
                tool_results = []

                for block in assistant_content:
                    if block.type != "tool_use":
                        continue

                    tool_name = block.name
                    tool_input = block.input
                    tool_id = block.id

                    logger.info(
                        "llm_tool_call",
                        tool=tool_name,
                        input=tool_input,
                        turn=turn,
                    )

                    result = await self._handle_tool_call(tool_name, tool_input)

                    # If resolve_address was called, capture the address
                    if tool_name == "resolve_address":
                        with contextlib.suppress(Exception):
                            resolved_address = Address(
                                street=tool_input["street"],
                                city=tool_input["city"],
                                state=tool_input["state"],
                                zipcode=tool_input.get("zipcode"),
                            )

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tool_id,
                        "content": json.dumps(result),
                    })

                messages.append({"role": "user", "content": tool_results})

            elif response.stop_reason == "end_turn":
                # Model finished without needing more tools
                break

        if resolved_address is None:
            raise LLMError(
                f"LLM failed to resolve address after {max_turns} turns: {query}"
            )

        logger.info(
            "llm_resolve_complete",
            resolved=resolved_address.to_search_query(),
        )
        return resolved_address

    async def _web_search(self, query: str) -> str:
        """Search the web via DuckDuckGo HTML and return result snippets."""
        import httpx
        from bs4 import BeautifulSoup

        from zillow_agent.config import get_random_user_agent

        headers = {
            "User-Agent": get_random_user_agent(),
            "Accept": "text/html",
            "Accept-Language": "en-US,en;q=0.9",
        }
        try:
            async with httpx.AsyncClient(
                timeout=10.0, follow_redirects=True
            ) as client:
                resp = await client.get(
                    "https://html.duckduckgo.com/html/",
                    params={"q": query},
                    headers=headers,
                )

            soup = BeautifulSoup(resp.text, "html.parser")
            results = []
            for r in soup.select(".result")[:5]:
                title_el = r.select_one(".result__title")
                snippet_el = r.select_one(".result__snippet")
                title = title_el.get_text(strip=True) if title_el else ""
                snippet = snippet_el.get_text(strip=True) if snippet_el else ""
                if title or snippet:
                    results.append(f"{title}\n{snippet}")

            return "\n\n".join(results) if results else "No results found."
        except Exception as e:
            logger.warning("web_search_failed", error=str(e))
            return f"Web search failed: {e}"

    async def _handle_tool_call(
        self, tool_name: str, tool_input: dict[str, Any]
    ) -> dict[str, Any]:
        """Handle a tool call from the LLM.

        search_web: searches the web for information
        resolve_address: just validates and returns the structured address
        fetch_zestimate: delegates to the deterministic fetcher
        report_result: passes through (terminal)
        """
        if tool_name == "search_web":
            query = tool_input.get("query", "")
            logger.info("web_search", query=query)
            text = await self._web_search(query)
            return {"status": "success", "results": text}

        if tool_name == "resolve_address":
            # The LLM already provided the structured address in tool_input
            return {
                "status": "resolved",
                "address": tool_input,
            }

        if tool_name == "fetch_zestimate":
            try:
                address = Address(
                    street=tool_input["street"],
                    city=tool_input["city"],
                    state=tool_input["state"],
                    zipcode=tool_input.get("zipcode"),
                )
                result = await self._fetcher.fetch(address)
                return {
                    "status": "success",
                    "zestimate": result.zestimate,
                    "zpid": result.zpid,
                    "source": result.source.value,
                }
            except ZillowAgentError as e:
                return {
                    "status": "error",
                    "error": str(e),
                    "error_type": type(e).__name__,
                }

        if tool_name == "report_result":
            return {"status": "reported", **tool_input}

        return {"status": "error", "error": f"Unknown tool: {tool_name}"}

    async def close(self) -> None:
        """Clean up resources."""
        await self._fetcher.close()
