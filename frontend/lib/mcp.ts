// The bench as an MCP server (spec/09): what a person pastes into their AI
// client to connect it. Pure strings — the token itself comes from POST
// /mcp/tokens and is shown once.

// Same name the server reports in serverInfo, so the client lists it under it.
export const MCP_SERVER_NAME = "quantax-bench";

/** The MCP endpoint on the API — never the frontend's own origin. */
export function mcpUrl(apiUrl: string): string {
  return `${apiUrl.replace(/\/+$/, "")}/mcp`;
}

/** One line for Claude Code. Single-quoted so a shell never expands the token. */
export function claudeCodeCommand(url: string, token: string): string {
  return (
    `claude mcp add --transport http ${MCP_SERVER_NAME} ${url} ` +
    `--header 'Authorization: Bearer ${token}'`
  );
}

/** The `mcpServers` block most other clients (Claude Desktop, Cursor…) read. */
export function mcpJsonConfig(url: string, token: string): string {
  const config = {
    mcpServers: {
      [MCP_SERVER_NAME]: {
        type: "http",
        url,
        headers: { Authorization: `Bearer ${token}` },
      },
    },
  };
  return JSON.stringify(config, null, 2);
}
