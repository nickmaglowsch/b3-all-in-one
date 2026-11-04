import { test } from "node:test";
import assert from "node:assert/strict";

import { compileRegime, parseRegime, topPctInert } from "./regime.ts";

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
