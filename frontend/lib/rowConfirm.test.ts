// Run: npm test (node --test, native TS — no runner dependency).
import { test } from "node:test";
import assert from "node:assert/strict";
import { arm, rowState, settle } from "./rowConfirm.ts";

test("arming opens one row's confirm at a time", () => {
  assert.equal(arm(null, "a", null), "a");
  assert.equal(arm("a", "b", null), "b");
  assert.equal(rowState("b", "b", null), "armed");
  assert.equal(rowState("a", "b", null), "idle");
});

test("while A deletes, Excluir on B changes nothing and B never reads as deleting", () => {
  // Confirmed A: A is busy (from the mutation's variables).
  let armed: string | null = "a";
  armed = arm(armed, "b", "a");
  assert.equal(armed, "a");
  assert.equal(rowState("a", armed, "a"), "busy");
  assert.equal(rowState("b", armed, "a"), "idle");
});

test("settling closes only its own row's confirm", () => {
  assert.equal(settle("a", "a"), null);
  // Had another row been armed since (a refused delete, then a new arm), A's
  // settle must leave it open.
  assert.equal(settle("b", "a"), "b");
  assert.equal(settle(null, "a"), null);
});
