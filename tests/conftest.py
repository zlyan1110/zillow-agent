"""Shared test fixtures for the Zillow Zestimate Agent."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from zillow_agent.config import AgentConfig
from zillow_agent.models import Address

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def bellevue_address() -> Address:
    """Standard test address: 14933 SE 45th Pl, Bellevue, WA."""
    return Address(
        street="14933 SE 45th Pl",
        city="Bellevue",
        state="WA",
        zipcode="98006",
    )


@pytest.fixture
def sample_address() -> Address:
    """A typical residential address for testing."""
    return Address(
        street="123 Main St",
        city="Seattle",
        state="WA",
        zipcode="98101",
    )


@pytest.fixture
def next_data_json() -> dict:
    """Sample __NEXT_DATA__ JSON for a property detail page."""
    path = FIXTURES_DIR / "next_data_detail.json"
    with open(path) as f:
        return json.load(f)


@pytest.fixture
def property_page_html() -> str:
    """Sample Zillow property page HTML with __NEXT_DATA__."""
    path = FIXTURES_DIR / "property_page.html"
    with open(path) as f:
        return f.read()


@pytest.fixture
def next_data_search_json() -> dict:
    """Sample __NEXT_DATA__ JSON for a search results page."""
    path = FIXTURES_DIR / "next_data_search.json"
    with open(path) as f:
        return json.load(f)


@pytest.fixture
def agent_config_no_llm() -> AgentConfig:
    """Agent config with LLM disabled (fast unit tests)."""
    return AgentConfig(enable_llm=False)


@pytest.fixture
def agent_config_full() -> AgentConfig:
    """Agent config with all features enabled."""
    return AgentConfig(enable_llm=True)
