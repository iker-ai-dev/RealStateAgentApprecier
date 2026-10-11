from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from app.agent_api import (
    appraise_property,
    ask_property_agent,
    get_property_context,
    list_properties,
)
from app.observability import init_sentry


mcp = FastMCP(
    "RetailScope AI",
    instructions=(
        "Use RetailScope AI tools to inspect shopping-center rent rolls, market "
        "demographics, sales comps, and valuation outputs. Start with "
        "list_shopping_centers when a property_id is unknown. Treat outputs as "
        "decision-support underwriting, not a certified appraisal."
    ),
)


@mcp.tool()
def list_shopping_centers() -> list[dict[str, Any]]:
    """List available shopping centers and their core acquisition metrics."""
    return list_properties()


@mcp.tool()
def get_shopping_center_context(property_id: str) -> dict[str, Any]:
    """Return property, tenant, market, comp, and valuation context for one center."""
    return get_property_context(property_id)


@mcp.tool()
def appraise_shopping_center(property_id: str) -> dict[str, Any]:
    """Run the commercial shopping-center valuation model for one center."""
    return appraise_property(property_id)


@mcp.tool()
def ask_retailscope(property_id: str, question: str) -> dict[str, Any]:
    """Ask the RetailScope AI agent an underwriting question about one center."""
    return ask_property_agent(property_id, question)


def main() -> None:
    init_sentry()
    mcp.run()


if __name__ == "__main__":
    main()
