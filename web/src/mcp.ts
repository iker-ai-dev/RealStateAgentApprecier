export const TOKEN_ENV = "RETAILSCOPE_MCP_TOKEN";
export function installCommands(endpoint: string): Record<string, string> {
  return {
    Codex: `codex mcp add retailscope-ai --url ${JSON.stringify(endpoint)} --bearer-token-env-var ${TOKEN_ENV}`,
    "Claude Code": `claude mcp add --transport http retailscope-ai ${JSON.stringify(endpoint)} --header "Authorization: Bearer \${${TOKEN_ENV}}"`,
    OpenCode: JSON.stringify(
      {
        mcp: {
          "retailscope-ai": {
            type: "remote",
            url: endpoint,
            enabled: true,
            oauth: false,
            headers: { Authorization: `Bearer {env:${TOKEN_ENV}}` },
          },
        },
      },
      null,
      2,
    ),
    "Generic JSON": JSON.stringify(
      {
        mcpServers: {
          "retailscope-ai": {
            type: "http",
            url: endpoint,
            headers: { Authorization: `Bearer \${${TOKEN_ENV}}` },
          },
        },
      },
      null,
      2,
    ),
  };
}
