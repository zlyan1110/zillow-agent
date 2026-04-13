"""CLI entry point for the Zillow Zestimate Agent.

Usage:
    zestimate "14933 SE 45th Place, Bellevue, WA 98006"
    zestimate "Nexhelm AI Location" --llm
    zestimate "123 Main St, Seattle, WA" --json
"""

from __future__ import annotations

import asyncio

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from zillow_agent.agent import ZestimateAgent
from zillow_agent.config import AgentConfig
from zillow_agent.exceptions import (
    AddressNotFoundError,
    AllStrategiesFailedError,
    LLMError,
    NoZestimateError,
    ParseError,
    ZillowAgentError,
    ZillowBlockedError,
)
from zillow_agent.logging import setup_logging
from zillow_agent.models import ZestimateRequest, ZestimateResponse

app = typer.Typer(
    name="zestimate",
    help="Fetch Zillow Zestimates for US property addresses.",
    no_args_is_help=True,
)
console = Console()


@app.command()
def lookup(
    address: str = typer.Argument(
        ...,
        help="Property address or place query",
    ),
    llm: bool = typer.Option(
        True,
        "--llm/--no-llm",
        help="Enable LLM for fuzzy address resolution",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output result as JSON",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Enable verbose logging",
    ),
    mock: bool = typer.Option(
        False,
        "--mock",
        help="Use fixture data instead of real API calls (no keys needed)",
    ),
    debug: bool = typer.Option(
        False,
        "--debug",
        help="Enable debug logging with JSON output",
    ),
) -> None:
    """Fetch the Zillow Zestimate for a US property address."""
    # Configure logging
    if debug:
        setup_logging(json_output=True, level="DEBUG")
    elif verbose:
        setup_logging(json_output=False, level="DEBUG")
    else:
        setup_logging(json_output=False, level="WARNING")

    # Mock mode: return fixture data without any API calls
    if mock:
        from zillow_agent.mock import mock_lookup

        result = mock_lookup(address)
        if result is None:
            console.print(
                f"[yellow]Mock mode:[/yellow] No fixture data for '{address}'. "
                "Known addresses: '14933 SE 45th Place, Bellevue, WA 98006'"
            )
            raise typer.Exit(code=1)
        if json_output:
            console.print(result.model_dump_json(indent=2))
        else:
            _print_rich_result(result)
        return

    # Build config
    config = AgentConfig(
        enable_llm=llm,
    )

    # Run the agent
    try:
        result = asyncio.run(_run_agent(address, config))
    except ZillowAgentError as e:
        _print_error(e)
        raise typer.Exit(code=1) from e
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted[/yellow]")
        raise typer.Exit(code=130) from None

    # Output result
    if json_output:
        console.print(result.model_dump_json(indent=2))
    else:
        _print_rich_result(result)


def _print_error(error: ZillowAgentError) -> None:
    """Print a user-friendly error message based on the exception type."""
    # Unwrap AllStrategiesFailedError to show root cause
    if isinstance(error, AllStrategiesFailedError) and error.errors:
        _, root = error.errors[0]
    else:
        root = error

    if isinstance(root, NoZestimateError):
        console.print(f"[yellow]No Zestimate available:[/yellow] {root}")
        console.print(
            "[dim]Not all properties have Zestimates — Zillow may not have "
            "enough data to estimate this property's value.[/dim]"
        )
    elif isinstance(root, AddressNotFoundError):
        console.print(f"[red]Address not found:[/red] {root}")
        console.print(
            "[dim]The address could not be found on Zillow. "
            "Check the spelling and try again.[/dim]"
        )
    elif isinstance(root, ZillowBlockedError):
        console.print(f"[red]Request blocked:[/red] {root}")
        console.print(
            "[dim]Zillow's anti-bot system blocked the request. "
            "Try again in a few minutes.[/dim]"
        )
    elif isinstance(root, ParseError):
        console.print(f"[red]Parse error:[/red] {root}")
        console.print(
            "[dim]Zillow may have changed their page structure. "
            "Try running with --debug for details.[/dim]"
        )
    elif isinstance(root, LLMError):
        console.print(f"[red]LLM error:[/red] {root}")
        console.print(
            "[dim]Try with --no-llm for standard addresses, "
            "or check your ANTHROPIC_API_KEY.[/dim]"
        )
    else:
        console.print(f"[red]Error:[/red] {root}")


async def _run_agent(address: str, config: AgentConfig) -> ZestimateResponse:
    """Run the agent and return the result."""

    agent = ZestimateAgent(config=config)
    try:
        request = ZestimateRequest(query=address)
        return await agent.run(request)
    finally:
        await agent.close()


def _print_rich_result(result: ZestimateResponse) -> None:
    """Pretty-print the result using Rich."""

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Field", style="bold")
    table.add_column("Value")

    table.add_row("Address", result.address.to_search_query())
    table.add_row("Zestimate", f"${result.zestimate:,}")
    if result.rent_zestimate:
        table.add_row("Rent Zestimate", f"${result.rent_zestimate:,}/mo")
    table.add_row("ZPID", str(result.zpid))
    table.add_row("Source", result.source.value)
    table.add_row("Latency", f"{result.latency_ms:.0f}ms")
    table.add_row("Used LLM", str(result.used_llm))
    table.add_row("Fetched At", result.fetched_at.strftime("%Y-%m-%d %H:%M:%S UTC"))

    console.print(Panel(table, title="Zillow Zestimate", border_style="green"))


def main() -> None:
    """Entry point for the CLI."""
    app()


if __name__ == "__main__":
    main()
