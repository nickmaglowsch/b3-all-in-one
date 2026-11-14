// Run: npm test (node --test, native TS — no runner dependency).
import { test } from "node:test";
import assert from "node:assert/strict";
import { asSet, isStale } from "./staleResult.ts";

test("no result yet is never stale", () => {
  assert.equal(isStale(undefined, { new_cash: 0 }), false);
});

test("the same request is fresh, whatever its key order or undefined fields", () => {
  assert.equal(isStale({ a: 1, b: [1, 2] }, { b: [1, 2], a: 1 }), false);
  assert.equal(isStale({ new_cash: 0, exclude: undefined }, { new_cash: 0 }), false);
});

test("a changed input makes the result stale", () => {
  assert.equal(isStale({ new_cash: 1000 }, { new_cash: 2000 }), true);
  assert.equal(
    isStale({ sells: [{ ticker: "PETR4", qty: 100 }] }, { sells: [{ ticker: "PETR4", qty: 50 }] }),
    true,
  );
  assert.equal(isStale({ sells: [] }, { sells: [], exclude: ["VALE3"] }), true);
});

test("a set-like list compares as a set through the key function", () => {
  type Body = { strategy_ids: string[]; freq: string };
  const key = (b: Body) => ({ ...b, strategy_ids: asSet(b.strategy_ids) });
  const sent: Body = { strategy_ids: ["A", "B"], freq: "ME" };
  assert.equal(isStale(sent, { strategy_ids: ["B", "A"], freq: "ME" }, key), false);
  assert.equal(isStale(sent, { strategy_ids: ["A"], freq: "ME" }, key), true);
  assert.equal(isStale(sent, { strategy_ids: ["A", "B"], freq: "W" }, key), true);
  assert.deepEqual(asSet(undefined), []);
});
