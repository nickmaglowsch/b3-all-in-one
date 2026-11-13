// Run: npm test (node --test, native TS — no runner dependency).
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  CLOSED,
  firstEnabled,
  fold,
  grouped,
  lastEnabled,
  matches,
  onKey,
  reconcile,
  step,
  typedQuery,
  type ComboOption,
} from "./combobox.ts";

const opt = (id: string, group = "g", disabledReason?: string): ComboOption => ({
  id,
  label: id,
  group,
  disabledReason,
});

// a, [b disabled], c, d — the arrows skip b.
const OPTS = [opt("a"), opt("b", "g", "sem cotação"), opt("c", "h"), opt("d", "h")];

test("matching folds accents and case, and wants every word", () => {
  assert.equal(fold("Ação PETRÓLEO"), "acao petroleo");
  assert.ok(matches("acao", "Ação"));
  assert.ok(matches("s&p etf", "SPDR S&P 500 ETF Trust"));
  assert.ok(!matches("s&p bond", "SPDR S&P 500 ETF Trust"));
  assert.ok(matches("", "anything"));
  assert.ok(matches("   ", "anything"));
});

test("stepping skips disabled options and stops at the ends", () => {
  assert.equal(step(OPTS, 0, 1), 2);
  assert.equal(step(OPTS, 2, -1), 0);
  assert.equal(step(OPTS, 3, 1), 3);
  assert.equal(step(OPTS, 0, -1), 0);
  assert.equal(firstEnabled(OPTS), 0);
  assert.equal(lastEnabled(OPTS), 3);
  const none = [opt("x", "g", "no")];
  assert.equal(firstEnabled(none), -1);
  assert.equal(lastEnabled(none), -1);
  assert.equal(lastEnabled([]), -1);
});

test("arrows open the list and move through it", () => {
  let r = onKey(CLOSED, "ArrowDown", OPTS);
  assert.deepEqual(r, { state: { open: true, active: 0 }, handled: true });
  r = onKey(r.state, "ArrowDown", OPTS);
  assert.equal(r.state.active, 2);
  r = onKey(r.state, "ArrowUp", OPTS);
  assert.equal(r.state.active, 0);
  // ArrowUp on a closed list opens it at the bottom.
  assert.deepEqual(onKey(CLOSED, "ArrowUp", OPTS).state, { open: true, active: 3 });
});

test("Home and End jump only while open; closed, they move the caret", () => {
  const open = { open: true, active: 2 };
  assert.equal(onKey(open, "Home", OPTS).state.active, 0);
  assert.equal(onKey(open, "End", OPTS).state.active, 3);
  assert.deepEqual(onKey(CLOSED, "Home", OPTS), { state: CLOSED, handled: false });
});

test("Enter chooses the active option and closes; never a disabled one", () => {
  assert.deepEqual(onKey({ open: true, active: 2 }, "Enter", OPTS), {
    state: CLOSED,
    select: 2,
    handled: true,
  });
  const onDisabled = onKey({ open: true, active: 1 }, "Enter", OPTS);
  assert.equal(onDisabled.select, undefined);
  assert.equal(onDisabled.state.open, true);
  // Closed, Enter is the form's, not the list's.
  assert.deepEqual(onKey(CLOSED, "Enter", OPTS), { state: CLOSED, handled: false });
});

test("Escape closes an open list, and clears the query on a closed one", () => {
  assert.deepEqual(onKey({ open: true, active: 0 }, "Escape", OPTS), { state: CLOSED, handled: true });
  assert.deepEqual(onKey(CLOSED, "Escape", OPTS), { state: CLOSED, clear: true, handled: true });
});

test("Tab closes without swallowing the focus move; other keys are typing", () => {
  assert.deepEqual(onKey({ open: true, active: 0 }, "Tab", OPTS), { state: CLOSED, handled: false });
  const typed = { open: true, active: 2 };
  assert.deepEqual(onKey(typed, "p", OPTS), { state: typed, handled: false });
});

test("a list that changes keeps the active option when it survives", () => {
  const open = { open: true, active: 2 }; // "c"
  const after = [opt("z"), opt("c", "h"), opt("d", "h")];
  assert.deepEqual(reconcile(open, OPTS, after), { open: true, active: 1 });
  // Gone, or now disabled: back to the first one that can be chosen.
  assert.deepEqual(reconcile(open, OPTS, [opt("z", "g", "no"), opt("y")]), { open: true, active: 1 });
  assert.deepEqual(reconcile(open, OPTS, []), { open: true, active: -1 });
  assert.deepEqual(reconcile(CLOSED, OPTS, []), CLOSED);
});

test("options are grouped in order, keeping their list index", () => {
  assert.deepEqual(
    grouped(OPTS).map((g) => [g.group, g.items.map((i) => [i.option.id, i.index])]),
    [
      ["g", [["a", 0], ["b", 1]]],
      ["h", [["c", 2], ["d", 3]]],
    ],
  );
  assert.deepEqual(grouped([]), []);
});

test("typing into a closed field starts a fresh search, not one for the choice + key", () => {
  const typed = (data: string) => ({ inputType: "insertText", data });
  // Open, the field holds the query as typed.
  assert.equal(typedQuery(true, "PETR4", "petr"), "petr");
  // Closed, it shows "PETR4": the keystroke is the query, wherever it landed.
  assert.equal(typedQuery(false, "PETR4", "PETR4V", typed("V")), "V");
  assert.equal(typedQuery(false, "PETR4", "PEVTR4", typed("V")), "V");
  // The whole choice selected (Tab focus) and typed over — even with the first or
  // last letter of the choice itself, which the text alone cannot tell apart.
  assert.equal(typedQuery(false, "PETR4", "P", typed("P")), "P");
  assert.equal(typedQuery(false, "PETR4", "4", typed("4")), "4");
  assert.equal(typedQuery(false, "momentum", "m", typed("m")), "m");
  // A deletion of any kind clears the search: never "PETR".
  assert.equal(typedQuery(false, "PETR4", "PETR", { inputType: "deleteContentBackward" }), "");
  assert.equal(typedQuery(false, "PETR4", "", { inputType: "deleteContentBackward" }), "");
  assert.equal(typedQuery(false, "PETR4", "PETR4", { inputType: "deleteByCut" }), "");
  // Nothing chosen yet: the field was empty, so the text is the query.
  assert.equal(typedQuery(false, "", "mom", typed("m")), "mom");
});

test("without the edit's own account, what replaced the choice is the query", () => {
  // A paste over the selection (data is null on paste) — or an old browser.
  const paste = { inputType: "insertFromPaste", data: null };
  assert.equal(typedQuery(false, "PETR4", "BTC", paste), "BTC");
  assert.equal(typedQuery(false, "PETR4", "P"), "P");
  assert.equal(typedQuery(false, "momentum", "m"), "m");
  // The choice still there whole: only what was added to it.
  assert.equal(typedQuery(false, "PETR4", "PETR4V"), "V");
  assert.equal(typedQuery(false, "SPY · EUA", "SPY · EUAb"), "b");
  // A repeated letter still counts once: "AAPL" + "A" is "A".
  assert.equal(typedQuery(false, "AAPL", "AAPLA"), "A");
  assert.equal(typedQuery(false, "AAPL", "AAAPL"), "A");
});
