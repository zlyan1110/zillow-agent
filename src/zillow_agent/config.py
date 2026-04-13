"""Configuration for the Zillow Zestimate Agent.

All tunable parameters in one place. Uses pydantic-settings pattern
for environment variable overrides.
"""

from __future__ import annotations

import random

from pydantic import BaseModel, Field

# --- User-Agent rotation pool ---
_USER_AGENTS = [
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) "
        "Gecko/20100101 Firefox/128.0"
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15"
    ),
]


def get_random_user_agent() -> str:
    """Return a random User-Agent string from the pool."""
    return random.choice(_USER_AGENTS)


def get_default_headers() -> dict[str, str]:
    """Return headers that mimic a real browser request."""
    return {
        "User-Agent": get_random_user_agent(),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
    }


# --- Zillow endpoints ---
ZILLOW_BASE_URL = "https://www.zillow.com"


class RetryConfig(BaseModel):
    """Retry parameters for transient failures."""

    max_attempts: int = Field(default=3, ge=1, le=10)
    wait_min_seconds: float = Field(default=1.0, ge=0.1)
    wait_max_seconds: float = Field(default=5.0, ge=0.5)
    retry_on_status: list[int] = Field(default=[429, 500, 502, 503, 504])


class TimeoutConfig(BaseModel):
    """Timeout budgets."""

    scrapfly_timeout: float = Field(default=25.0, description="Seconds for Scrapfly strategy")
    total_timeout: float = Field(default=30.0, description="Total pipeline timeout")


class LLMConfig(BaseModel):
    """LLM layer configuration."""

    model: str = Field(default="claude-sonnet-4-20250514")
    max_tokens: int = Field(default=1024)
    temperature: float = Field(default=0.0, description="Zero for deterministic address parsing")


class ScrapflyConfig(BaseModel):
    """Scrapfly anti-scraping proxy configuration."""

    api_key: str = Field(default="", description="Scrapfly API key (env: SCRAPFLY_API_KEY)")
    base_url: str = Field(
        default="https://api.scrapfly.io/scrape",
        description="Scrapfly scrape API endpoint",
    )
    asp: bool = Field(default=True, description="Enable Anti-Scraping Protection bypass")
    render_js: bool = Field(default=False, description="JS rendering (False preserves NEXT_DATA)")
    country: str = Field(default="US", description="Proxy country for geo-targeting")


class AgentConfig(BaseModel):
    """Top-level agent configuration."""

    retry: RetryConfig = Field(default_factory=RetryConfig)
    timeout: TimeoutConfig = Field(default_factory=TimeoutConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    scrapfly: ScrapflyConfig = Field(default_factory=ScrapflyConfig)
    enable_llm: bool = Field(default=True, description="Set False to force deterministic-only mode")
    cache_zpid: bool = Field(default=True, description="Cache address->ZPID mappings in memory")
