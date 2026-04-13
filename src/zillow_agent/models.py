"""Typed contracts for the Zillow Zestimate Agent.

All data flowing through the pipeline is validated by these models.
Consumers always know the shape, source, and freshness of data.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator


class StrategyName(StrEnum):
    """Identifies which fetch strategy produced a result."""

    SCRAPFLY = "scrapfly"


class Address(BaseModel):
    """Normalized US property address."""

    street: str = Field(..., min_length=1, description="Street address including unit/apt")
    city: str = Field(..., min_length=1)
    state: str = Field(..., min_length=2, max_length=2, description="Two-letter state code")
    zipcode: str | None = Field(default=None, description="5-digit or 5+4 ZIP code")

    @field_validator("state")
    @classmethod
    def uppercase_state(cls, v: str) -> str:
        return v.upper()

    @field_validator("zipcode")
    @classmethod
    def validate_zip(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not re.match(r"^\d{5}(-\d{4})?$", v):
            msg = f"Invalid ZIP code format: {v}"
            raise ValueError(msg)
        return v

    def to_zillow_slug(self) -> str:
        """Convert to Zillow URL-friendly slug.

        Example: '14933 SE 45th Pl, Bellevue, WA 98006'
                 -> '14933-SE-45th-Pl-Bellevue-WA-98006'
        """
        parts = [self.street, self.city, self.state]
        if self.zipcode:
            parts.append(self.zipcode.split("-")[0])
        raw = ", ".join(parts)
        slug = re.sub(r"[^\w\s-]", "", raw)
        slug = re.sub(r"[\s]+", "-", slug.strip())
        return slug

    def to_search_query(self) -> str:
        """Format as a search-friendly string."""
        parts = [self.street, self.city, self.state]
        if self.zipcode:
            parts.append(self.zipcode)
        return ", ".join(parts)


class ZestimateRequest(BaseModel):
    """Input to the agent - can be any form of address query."""

    query: str = Field(..., min_length=1, max_length=500, description="Address or place query")

    @field_validator("query")
    @classmethod
    def strip_whitespace(cls, v: str) -> str:
        return v.strip()


class ZestimateResponse(BaseModel):
    """Output from the agent - the Zestimate value with metadata."""

    address: Address
    zestimate: int = Field(..., gt=0, description="Zestimate value in USD")
    rent_zestimate: int | None = Field(default=None, description="Rental Zestimate in USD/month")
    zpid: int = Field(..., description="Zillow Property ID")
    source: StrategyName = Field(..., description="Which strategy produced this result")
    fetched_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="UTC timestamp when data was fetched",
    )
    latency_ms: float = Field(..., ge=0, description="End-to-end fetch latency in milliseconds")
    used_llm: bool = Field(default=False, description="Whether LLM was invoked for this query")

    @field_validator("zestimate")
    @classmethod
    def reasonable_value(cls, v: int) -> int:
        if v < 10_000:
            msg = f"Zestimate {v} is below $10,000 minimum sanity check"
            raise ValueError(msg)
        if v > 500_000_000:
            msg = f"Zestimate {v} exceeds $500M sanity cap"
            raise ValueError(msg)
        return v


class ZillowSearchResult(BaseModel):
    """A single result from Zillow's search API."""

    zpid: int
    address: str
    street: str | None = None
    city: str | None = None
    state: str | None = None
    zipcode: str | None = None
    price: int | None = None
    zestimate: int | None = None
    home_type: str | None = None
    detail_url: str | None = None


class InputClassification(StrEnum):
    """Classification of user input type."""

    STANDARD_ADDRESS = "standard_address"
    FUZZY_QUERY = "fuzzy_query"


class RouterDecision(BaseModel):
    """Result of the fast-path router."""

    classification: InputClassification
    address: Address | None = None
    reason: str = ""
