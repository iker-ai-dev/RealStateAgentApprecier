import json

PUBLIC_MCP_URL = "https://retailscope-ai-demo.onrender.com/mcp"


def remote_install_commands():
    return {
        "Codex": f"codex mcp add retailscope-ai --url {PUBLIC_MCP_URL}",
        "Claude Code": f"claude mcp add --transport http retailscope-ai {PUBLIC_MCP_URL}",
        "OpenCode": json.dumps({"mcp": {"retailscope-ai": {
            "type": "remote", "url": PUBLIC_MCP_URL, "enabled": True,
        }}}, indent=2),
        "Generic JSON": json.dumps({"mcpServers": {"retailscope-ai": {
            "type": "http", "url": PUBLIC_MCP_URL,
        }}}, indent=2),
    }
