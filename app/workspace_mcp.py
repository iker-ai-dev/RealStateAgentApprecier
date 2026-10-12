"""MCP tools operate on the same authenticated workspace as the browser API."""
import asyncio
import os
from typing import Any
from contextlib import nullcontext

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from app.observability import capture_exception, start_agent_transaction


async def _call(callback, *args, workflow=None):
    context = start_agent_transaction(workflow) if workflow else nullcontext()
    with context:
        try:
            return await asyncio.to_thread(callback, *args)
        except Exception as exc:
            capture_exception(exc)
            raise ValueError("Workspace operation failed. Check the inputs, provider access, and usage limits.") from None


def create_workspace_mcp(*, list_leads, get_job, start_research, chat, calculate):
    hosts = ["localhost", "localhost:*", "127.0.0.1", "127.0.0.1:*", "testserver",
             "retailscope-ai-demo.onrender.com"]
    external = os.getenv("RENDER_EXTERNAL_HOSTNAME")
    if external:
        hosts.append(external)
    server = FastMCP("RetailScope", stateless_http=True, json_response=True,
                     streamable_http_path="/mcp",
                     transport_security=TransportSecuritySettings(
                         enable_dns_rebinding_protection=True, allowed_hosts=hosts,
                         allowed_origins=["http://localhost:*", "http://127.0.0.1:*",
                                          "https://retailscope-ai-demo.onrender.com"]
                         + (["https://" + external] if external else [])))
    read = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
    paid = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True)

    def request(ctx):
        req = ctx.request_context.request
        if req is None or not getattr(req.state, "owner", None):
            raise ValueError("An authenticated workspace token is required.")
        return req

    @server.tool(annotations=read)
    async def list_research_leads(ctx: Context) -> dict[str, Any]:
        """List actual saved leads from your connected RetailScope workspace."""
        return await _call(list_leads, request(ctx), workflow="retailscope_ai.mcp.list_leads")

    @server.tool(annotations=paid)
    async def discover_retail_leads(query: str, ctx: Context) -> dict[str, Any]:
        """Start bounded live Nimble research and Cortex analysis. Incurs provider usage.

        Poll get_research_run using the returned job ID; no sample results are used.
        """
        return await _call(start_research, query, request(ctx), workflow="retailscope_ai.mcp.research")

    @server.tool(annotations=read)
    async def get_research_run(job_id: str, ctx: Context) -> dict[str, Any]:
        """Read your background research status, source leads, and analysis."""
        return await _call(get_job, job_id, request(ctx), workflow="retailscope_ai.mcp.research_status")

    @server.tool(annotations=paid)
    async def ask_research_agent(message: str, ctx: Context, lead_id: str | None = None,
                                 conversation_id: str | None = None) -> dict[str, Any]:
        """Ask Cortex to analyze your saved evidence. Incurs Snowflake model usage."""
        return await _call(chat, {"message": message, "lead_id": lead_id,
                                              "conversation_id": conversation_id}, request(ctx))

    @server.tool(annotations=read)
    async def calculate_investment_scenario(assumptions: dict[str, Any], ctx: Context) -> dict[str, Any]:
        """Compute cash flows, IRR and equity multiple from explicit assumptions.

        Required: purchase_price, annual_noi, exit_cap_rate (decimal).
        Optional: hold_years, noi_growth, loan_to_value, interest_rate, initial_capex,
        annual_capex, acquisition_cost_pct, disposition_cost_pct, amortization_years,
        interest_only. All rates are decimals. No model inference is billed.
        """
        request(ctx)
        return await _call(calculate, assumptions, workflow="retailscope_ai.mcp.scenario")

    return server
