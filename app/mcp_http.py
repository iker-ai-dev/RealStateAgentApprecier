"""Public, read-only demo MCP; private Snowflake access remains stdio-only."""
import os

from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import PlainTextResponse

from app.mcp_server import mcp
from app.observability import init_sentry


def configure_public_http():
    os.environ["RETAILSCOPE_PUBLIC_DEMO"] = "true"
    os.environ.pop("RETAILSCOPE_REQUIRE_SNOWFLAKE", None)
    hosts = ["localhost", "localhost:*", "127.0.0.1", "127.0.0.1:*",
             "retailscope-ai-demo.onrender.com"]
    external = os.getenv("RENDER_EXTERNAL_HOSTNAME")
    if external:
        hosts.append(external)
    mcp.settings.host = "127.0.0.1"
    mcp.settings.port = 8502
    mcp.settings.stateless_http = True
    mcp.settings.json_response = True
    mcp.settings.transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=hosts,
        allowed_origins=["https://retailscope-ai-demo.onrender.com",
                         "http://localhost:*", "http://127.0.0.1:*"]
        + ([f"https://{external}"] if external else []),
    )


@mcp.custom_route("/healthz", methods=["GET"])
async def health(request):
    return PlainTextResponse("RetailScope public demo MCP: ok")


def main():
    configure_public_http()
    init_sentry()
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
