import { strict as assert } from "node:assert";
import test from "node:test";

import { claudeCodeCommand, mcpJsonConfig, mcpUrl } from "./mcp.ts";

test("o endpoint MCP fica na API, com ou sem barra no fim", () => {
  assert.equal(mcpUrl("https://api.quantax.com.br"), "https://api.quantax.com.br/mcp");
  assert.equal(mcpUrl("http://localhost:8000/"), "http://localhost:8000/mcp");
});

test("o comando do Claude Code leva o token entre aspas simples", () => {
  const cmd = claudeCodeCommand("https://api.x/mcp", "qtx_mcp_a$b");
  assert.equal(
    cmd,
    "claude mcp add --transport http quantax-bench https://api.x/mcp " +
      "--header 'Authorization: Bearer qtx_mcp_a$b'",
  );
});

test("a configuração JSON é válida e aponta para o mesmo servidor", () => {
  const parsed = JSON.parse(mcpJsonConfig("https://api.x/mcp", "qtx_mcp_t"));
  assert.deepEqual(parsed.mcpServers["quantax-bench"], {
    type: "http",
    url: "https://api.x/mcp",
    headers: { Authorization: "Bearer qtx_mcp_t" },
  });
});
