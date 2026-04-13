# Zillow Zestimate Agent

A production-grade Python agent that fetches Zillow Zestimates (estimated home values) for US property addresses. Handles both structured addresses and natural language queries like company names or landmarks.

## Quick Start

```bash
# Install
pip install -e ".[dev]"

# Set API key (required for fetching)
export SCRAPFLY_API_KEY=your_key    # https://scrapfly.io

# Standard address (fast path — skips LLM)
zestimate "14933 SE 45th Place, Bellevue, WA 98006"

# Natural language query (LLM resolves address, then fetches)
export ANTHROPIC_API_KEY=sk-...
zestimate "Nexhelm AI Location"

# JSON output
zestimate "123 Main St, Seattle, WA 98101" --json

# Deterministic-only mode (no LLM)
zestimate "123 Main St, Seattle, WA 98101" --no-llm

# Mock mode (no API keys needed — uses fixture data for demo)
zestimate "14933 SE 45th Place, Bellevue, WA 98006" --mock
```

## Architecture

### Pipeline Flow

```
User Input
  → Fast-path Router (regex classification)
     → Standard address (~80%) → skip LLM, go direct to fetcher
     → Fuzzy / unknown query   → LLM resolves to structured address
  → Scrapfly Fetcher (anti-bot proxy, single fetch strategy)
     → Fetch Zillow search URL via Scrapfly ASP
     → Detect page type from __NEXT_DATA__ JSON
        → Search page → match address against results
           → Match has Zestimate → return (1 Scrapfly call, ~3-6s)
           → Match has ZPID only → fetch detail page (2 calls, ~6-10s)
        → Detail page → parse Zestimate directly (1 call, ~3-6s)
        → Unknown → fallback parsing + render_js retry
  → Validation ($10k–$500M sanity range)
  → ZestimateResponse
```

### Two-Path Design

**Fast path** handles ~80% of queries: a regex check recognizes standard US address formats (e.g. `"123 Main St, Seattle, WA 98101"`) and skips the LLM entirely, going straight to Scrapfly.

**LLM path** handles the remaining ~20%: fuzzy queries like `"Nexhelm AI Location"` or `"Bill Gates house in Medina"` go through Claude, which can search the web for unknown places, resolve to a structured address, then fetch. The LLM sees 4 high-level tools:

| Tool | Purpose |
|------|---------|
| `search_web` | Look up addresses of unknown companies/landmarks via DuckDuckGo |
| `resolve_address` | Structure a known address into street/city/state/zip |
| `fetch_zestimate` | Fetch the Zestimate for a structured address |
| `report_result` | Report the final result or explain failure |

The LLM never sees low-level details (HTTP calls, HTML parsing, retry logic). This boundary prevents hallucination from contaminating the data path.

## Design Decisions

**Why not LangChain/LangGraph?** The agent has 4 tools and runs at most 8 turns. LangChain would add ~50 transitive dependencies for a problem that's solved by a 40-line tool dispatch loop. The hand-rolled ReAct loop gives full control over logging, timeouts, and error handling with zero framework overhead.

**Why is the LLM boundary drawn here?** The LLM only handles *intent understanding* — resolving "what did the user mean?" Deterministic code handles all data retrieval and parsing. This keeps accuracy at 99%+ by ensuring the Zestimate value is extracted exactly as Zillow displays it, never generated or approximated by the LLM.

**Why hybrid search→detail?** Zillow URLs often return a search results page rather than a property detail page. The strategy detects the page type from `__NEXT_DATA__` JSON, matches the queried address against search results using street number + name scoring, and only fetches a second detail page if the search result doesn't include the Zestimate.

**Why `render_js=false`?** Scrapfly can render JavaScript via headless browser, but this hydrates away the `__NEXT_DATA__` JSON blob — our most reliable parsing source. Disabling JS rendering preserves it, and is also faster (~3s vs ~8s). JS rendering is only used as a last-resort retry.

**Why DuckDuckGo for web search?** When the LLM encounters an unknown company or landmark, it needs to look up the address rather than fabricate one. DuckDuckGo HTML search requires no API key and uses dependencies already in the project (httpx + BeautifulSoup).

**Why Scrapfly as the sole strategy?** Zillow uses PerimeterX/Akamai anti-bot protection — plain HTTP requests get 403. Scrapfly's ASP (Anti-Scraping Protection) handles this via residential proxies and real browser clusters with ~99% success rate.

## Project Structure

```
src/zillow_agent/
    __init__.py              # Package version
    models.py                # Pydantic models (Address, ZestimateResponse, etc.)
    config.py                # Settings, timeouts, retry params, ScrapflyConfig
    exceptions.py            # Custom exception hierarchy
    normalizer.py            # Address normalization, fast-path router, address matching
    parsers.py               # HTML/JSON parsing, page type detection, search result extraction
    fetcher.py               # Scrapfly-based fetcher with retry (tenacity)
    agent.py                 # LLM agent layer (Claude tool use + web search)
    cli.py                   # Typer CLI entry point
    logging.py               # structlog configuration
    strategies/
        base.py              # FetchStrategy ABC
        scrapfly_strategy.py # Scrapfly anti-bot proxy strategy
scripts/
    debug_scrapfly.py        # Diagnostic tool: dump Scrapfly response for debugging
tests/
    conftest.py              # Shared fixtures
    test_normalizer.py       # Address parsing and classification tests
    test_strategies.py       # Parser tests with HTML/JSON fixtures
    test_agent.py            # Integration tests (mocked at fetcher level)
    fixtures/                # Captured HTML/JSON snapshots from Zillow
```

## Testing

```bash
# Run all tests
pytest -v

# With coverage
pytest --cov=zillow_agent --cov-report=term-missing

# Lint
ruff check src/ tests/

# Type check
mypy src/
```

## Configuration

All settings are in `AgentConfig` (Pydantic models) and can be overridden programmatically:

```python
from zillow_agent.config import AgentConfig, LLMConfig, ScrapflyConfig, TimeoutConfig
from zillow_agent.agent import ZestimateAgent

config = AgentConfig(
    enable_llm=True,
    timeout=TimeoutConfig(scrapfly_timeout=25.0, total_timeout=30.0),
    scrapfly=ScrapflyConfig(render_js=False, asp=True),
    llm=LLMConfig(model="claude-sonnet-4-20250514", temperature=0.0),
)
agent = ZestimateAgent(config=config)
```

## Error Handling

Custom exception hierarchy enables precise error handling:

```python
from zillow_agent.exceptions import (
    AddressNotFoundError,       # Address doesn't exist on Zillow
    NoZestimateError,           # Property found but no Zestimate available
    ZillowBlockedError,         # Anti-bot detection triggered (retryable)
    ParseError,                 # Page structure changed
    AllStrategiesFailedError,   # Scrapfly failed after retries
    LLMError,                   # LLM disabled but fuzzy query received
)
```

## Trade-offs & Known Limitations

- **Single fetch strategy**: Depends entirely on Scrapfly — if their service is down or rate-limited, fetching fails. A secondary strategy (e.g. residential proxy pool) would add redundancy.
- **Web search reliability**: DuckDuckGo HTML search may be rate-limited under heavy use. Could be replaced with a dedicated search API.
- **Single-turn only**: No conversation memory — each query is independent. Cannot handle follow-ups like "what about the house next door?"
- **Fixture staleness**: Test fixtures are snapshots of Zillow pages at a point in time. If Zillow changes their `__NEXT_DATA__` structure, fixtures need refreshing.
- **Zestimate range cap**: Sanity check rejects values outside $10k–$500M, which could reject extreme properties.

## Future Work

- **Redis/SQLite ZPID cache**: Persist address-to-ZPID mappings across runs
- **FastAPI server**: REST endpoint for integration with other services
- **Batch mode**: Process a CSV of addresses concurrently
- **Proxy rotation**: Residential proxy pool for high-volume usage
- **Grafana dashboard**: Monitor strategy success rates and latency P50/P99
- **Webhook alerts**: Notify when a strategy starts failing (Zillow changed their structure)

## License

MIT
