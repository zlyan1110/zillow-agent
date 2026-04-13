"""Custom exception hierarchy for the Zillow Zestimate Agent.

Callers can distinguish between retryable transient errors and
permanent failures without inspecting error messages.
"""

from __future__ import annotations


class ZillowAgentError(Exception):
    """Base exception for all agent errors."""

    def __init__(self, message: str, *, strategy: str | None = None) -> None:
        self.strategy = strategy
        super().__init__(message)


class AddressNotFoundError(ZillowAgentError):
    """The address does not exist on Zillow or returned zero results.

    This is a permanent failure - retrying will not help.
    """


class ZillowBlockedError(ZillowAgentError):
    """Zillow's anti-bot system blocked the request.

    This is a transient failure - retrying with a different strategy
    or after a delay may succeed.
    """


class ParseError(ZillowAgentError):
    """Failed to parse the expected data from Zillow's response.

    Usually means Zillow changed their page structure or API schema.
    Check the extraction logic against current Zillow markup.
    """


class AllStrategiesFailedError(ZillowAgentError):
    """All fetch strategies were attempted and none succeeded.

    Contains details about each strategy's failure.
    """

    def __init__(self, errors: list[tuple[str, Exception]]) -> None:
        self.errors = errors
        details = "; ".join(f"{name}: {err}" for name, err in errors)
        super().__init__(f"All strategies failed: {details}")


class NoZestimateError(ZillowAgentError):
    """The property was found on Zillow but does not have a Zestimate.

    Not all properties have Zestimates — Zillow may not have enough
    data to generate an estimate for this address.
    """


class LLMError(ZillowAgentError):
    """The LLM layer failed to process the query.

    Could be an API error, timeout, or unexpected response format.
    """


class TimeoutExceededError(ZillowAgentError):
    """The total timeout budget for the pipeline was exceeded."""
