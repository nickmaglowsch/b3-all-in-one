// Run: npm test (node --test, native TS — no runner dependency).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import {
  NUMERIC_FLAGS,
  REGIME_FLAGS,
  compileRegime,
  flagOptions,
  parseRegime,
  topPctInert,
} from "./regime.ts";

// The engine's list of the flags a regime input may read — what validate_spec
// accepts on save. Parsed from the source so the two cannot drift apart.
function engineRegimeFlags(): Set<string> {
  const src = readFileSync(
    new URL("../../engine/backtests/core/regimes.py", import.meta.url),
    "utf8",
  );
  const block = src.match(/^REGIME_FLAGS = \(([\s\S]*?)^\)/m);
  assert.ok(block, "REGIME_FLAGS tuple not found in regimes.py");
  return new Set([...block[1].matchAll(/"([a-z_0-9]+)"/g)].map((m) => m[1]));
}

test("every flag the picker offers is one the engine accepts", () => {
  const engine = engineRegimeFlags();
  for (const flag of REGIME_FLAGS) assert.ok(engine.has(flag), flag);
});

test("the picker does not offer the per-ticker above_ma200", () => {
  // dates x tickers: a regime input on it read False on every rebalance.
  assert.ok(!(REGIME_FLAGS as readonly string[]).includes("above_ma200"));
});

test("a stored off-list flag stays visible in the picker", () => {
  assert.deepEqual(flagOptions("is_easing"), REGIME_FLAGS);
  assert.deepEqual(flagOptions("above_ma200"), ["above_ma200", ...REGIME_FLAGS]);
});

// The drawdown-zoned BTC tilt an MCP client writes (platform mcp_guide.py).
const TILT = {
  add_ibov: false,
  park_when_too_few: true,
  inputs: {
    deep: { flag: "bench_drawdown", offset: 1, gt: 0.5 },
    cheap: { flag: "bench_drawdown", offset: 1, gt: 0.25 },
  },
  rules: [
    { deep: true, exposure: 1 },
    { cheap: true, exposure: 0.6, top_pct: 0.1 },
    { default: true, exposure: 0.2 },
  ],
};

test("an exposure book survives the form's round trip", () => {
  // Read as a condition, `exposure` was pruned on compile and the saved
  // strategy came back fully invested on every rule.
  assert.deepEqual(compileRegime(parseRegime(TILT)), TILT);
});

test("exposure is an action, not an input the rule matches", () => {
  const v = parseRegime(TILT);
  assert.deepEqual(v.rules[1].conds, { cheap: true });
  assert.equal(v.rules[1].exposure, "0.6");
  assert.equal(v.rules[2].kind, "default");
});

test("a rule without exposure compiles without the key", () => {
  const v = parseRegime({ inputs: {}, rules: [{ default: true }] });
  assert.equal(v.rules[0].exposure, "");
  assert.deepEqual(compileRegime(v).rules, [{ default: true }]);
});

test("a parked rule drops exposure rather than send both", () => {
  const v = parseRegime(TILT);
  v.rules[0] = { ...v.rules[0], action: "park" };
  assert.deepEqual((compileRegime(v).rules as unknown[])[0], { deep: true, park: true });
});

test("an exposure-only overlay still leaves selection.top_pct live", () => {
  assert.equal(
    topPctInert(parseRegime({ inputs: {}, rules: [{ min_true: 1, exposure: 0.5 }] })),
    false,
  );
});

test("the numeric flags are the engine's, so the gt hint matches the save check", () => {
  const src = readFileSync(
    new URL("../../engine/backtests/core/regimes.py", import.meta.url),
    "utf8",
  );
  const block = src.match(/^NUMERIC_FLAGS = frozenset\(\{([\s\S]*?)\}\)/m);
  assert.ok(block, "NUMERIC_FLAGS not found in regimes.py");
  const daily = src.match(/^DAILY_REGIME_FLAGS = \(([^)]*)\)/m);
  assert.ok(daily, "DAILY_REGIME_FLAGS not found in regimes.py");
  const names = (s: string) => [...s.matchAll(/"([a-z_0-9]+)"/g)].map((m) => m[1]);
  const engine = new Set([...names(block[1]), ...(block[1].includes("*DAILY_REGIME_FLAGS") ? names(daily[1]) : [])]);
  assert.deepEqual(new Set(NUMERIC_FLAGS), engine);
});
