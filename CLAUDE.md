# CLAUDE.md - Project Intelligence for Zillow Zestimate Agent

## Project Overview

A production-grade Python agent that fetches Zillow Zestimates for US property addresses.
Target: >= 99% exact-match accuracy against Zillow's currently displayed estimate.

## Architecture

### Core Design: Hybrid LLM + Deterministic Pipeline

The agent uses an LLM (Claude) for **intent understanding only**, and deterministic code for
all data fetching, parsing, and validation. A fast-path router skips the LLM entirely when
the input matches a standard US address format (~80% of queries).

### Pipeline Flow

```
User Input
  -> Fast-path Router (regex check)
     -> Standard address? -> skip LLM, go direct to fetcher
     -> Fuzzy/ambiguous?  -> LLM resolves to structured address
  -> Scrapfly Fetcher (anti-bot proxy, ~3-6s typical)
     -> Fetch search URL -> detect page type from __NEXT_DATA__
        -> Search page? -> match address against results
           -> Match has zestimate? -> return (1 call, ~3-6s)
           -> Match has ZPID only? -> fetch detail page (2 calls, ~6-10s)
        -> Detail page? -> parse directly (1 call, ~3-6s)
        -> Unknown? -> fallback parsing + render_js retry
  -> Validation Layer (type check + $10k-$500M range + freshness)
  -> Output: ZestimateResponse
```

### LLM Boundary (Critical Design Decision)

LLM OWNS (judgment tasks):
- Fuzzy address understanding ("Nexhelm AI Location" -> structured address)
- Multi-result disambiguation (3 matches -> pick best)
- Error explanation (why lookup failed, in natural language)

CODE OWNS (deterministic tasks):
- HTTP requests (httpx + retry + timeout)
- HTML/JSON parsing (BeautifulSoup + recursive JSON search + regex)
- Value extraction (int parse)
- Retry logic (tenacity + exponential backoff)

The LLM sees exactly 3 tools:
- resolve_address(query) -> structured address
- fetch_zestimate(street, city, state, zip) -> value
- report_result(success, explanation) -> final output

The LLM does NOT see low-level tools like scrapfly_fetch or parse_html.

## Key Files

```
src/zillow_agent/
  __init__.py          - Package init, version
  models.py            - Pydantic models (ZestimateRequest, ZestimateResponse, Address)
  config.py            - Settings, headers, timeouts, ScrapflyConfig
  exceptions.py        - Custom exception hierarchy
  normalizer.py        - Address normalization, fast-path router, address matching
  parsers.py           - HTML/JSON parsing, page type detection, search result extraction
  strategies/
    __init__.py
    base.py                - FetchStrategy ABC
    scrapfly_strategy.py   - Scrapfly anti-bot proxy
  fetcher.py           - Scrapfly-based fetcher with retry
  agent.py             - LLM agent layer (Claude tool use)
  cli.py               - Typer CLI entry point
  logging.py           - structlog configuration
scripts/
  debug_scrapfly.py    - Diagnostic tool: dumps Scrapfly response for debugging
tests/
  conftest.py          - Shared fixtures
  test_normalizer.py   - Address parsing tests
  test_strategies.py   - Parser tests with HTML fixtures
  test_agent.py        - Integration tests (mocked at fetcher level)
  fixtures/            - Captured HTML/JSON snapshots
```

## Tech Stack

- Python 3.11+
- httpx (async HTTP client — also used for Scrapfly REST API)
- pydantic v2 (data validation)
- tenacity (retry with backoff)
- structlog (structured logging)
- typer (CLI)
- anthropic (Claude API for LLM layer)
- beautifulsoup4 + lxml (HTML parsing)
- pytest + pytest-asyncio (testing)

## Design Patterns

- **Fast-Path Router**: Regex match on standard address format skips LLM (~80% of requests)
- **Hybrid Search→Detail**: Detects search vs detail pages; matches address against search results, fetches detail page only if needed
- **Address Matching**: Scores search results by street number, name, city, state to find the correct property
- **Dual render_js Retry**: Last-resort fallback retries with opposite `render_js` if all parsing fails
- **Recursive JSON Search**: Parser recursively traverses nested dicts to find zestimate data
- **Protocol-based DI**: AddressResolver protocol allows swapping LLM for regex/local model

## Latency Targets

- Fast path (single fetch, search has zestimate): 3 - 6s
- Fast path (two fetches, search → detail): 6 - 10s
- LLM path (fuzzy address + Scrapfly): 5 - 12s
- Total timeout budget: 30s

## Error Handling

Custom exception hierarchy:
- ZillowAgentError (base)
  - AddressNotFoundError (address doesn't exist on Zillow)
  - NoZestimateError (property found but no Zestimate available)
  - ZillowBlockedError (anti-bot detection triggered)
  - ParseError (page structure changed, parsing failed)
  - AllStrategiesFailedError (Scrapfly failed after retries)
  - LLMError (LLM disabled but fuzzy query received)

## Testing Strategy

- Snapshot testing: real Zillow HTML/JSON saved as fixtures for deterministic parsing tests
- Unit tests: parser functions tested with fixture data
- Integration tests: full pipeline with mocked fetcher
- Live eval (manual): small sample against real Zillow for release validation

## Development Commands

```bash
# Install in dev mode
pip install -e ".[dev]"

# Set API key (required)
export SCRAPFLY_API_KEY=your_scrapfly_key    # https://scrapfly.io

# Run tests
pytest -v

# Run the CLI
python -m zillow_agent "14933 SE 45th Place, Bellevue, WA 98006"

# Or after install:
zestimate "14933 SE 45th Place, Bellevue, WA 98006"

# Debug Scrapfly response (when parsing fails)
python scripts/debug_scrapfly.py "14933 SE 45th Place, Bellevue, WA 98006"
```

## Important Notes

- **Scrapfly is the sole fetch strategy** — requires `SCRAPFLY_API_KEY` env var
  - API: `GET https://api.scrapfly.io/scrape?url=<url>&asp=true&render_js=false&country=US`
  - Returns raw HTML in `response["result"]["content"]`
  - Free tier: ~1000 credits/month at https://scrapfly.io
- **Default `render_js=false`** — preserves `__NEXT_DATA__` JSON for reliable parsing; `render_js=true` hydrates it away
- `__NEXT_DATA__` JSON structure changes with Zillow's Next.js builds — parser uses recursive search to handle path changes
- **Search URL returns search results page** — strategy detects page type and matches address against search results before extracting zestimate
- Zestimate sanity range: $10,000 - $500M. Values outside this range are rejected as parsing errors
- ZPID (Zillow Property ID) is stable — cache address->ZPID mappings
- Anti-bot: Zillow uses PerimeterX/Akamai — plain HTTP gets 403, requires proxy service
- Never store or redistribute raw Zillow data at scale
