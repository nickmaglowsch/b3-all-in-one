// Run: npm test (node --test, native TS — no runner dependency).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { REGIME_FLAGS, flagOptions } from "./regime.ts";

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
