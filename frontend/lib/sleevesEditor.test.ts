// Run: npm test (node --test, native TS — no runner dependency).
import { test } from "node:test";
import assert from "node:assert/strict";
import type { LiveSleeve, SleeveRow } from "./portfolioSleeves.ts";
import {
  editorReducer as r,
  INITIAL,
  needsSync,
  type Editor,
  type EditorAction,
} from "./sleevesEditor.ts";

const row = (ref: string, pct: string, uid = ref): SleeveRow => ({
  uid,
  strategy_ref: ref,
  target_pct: pct,
});
const SERVER: LiveSleeve[] = [{ strategy_ref: "A", target_pct: 100 } as LiveSleeve];
const SERVER2: LiveSleeve[] = [{ strategy_ref: "B", target_pct: 100 } as LiveSleeve];

const run = (e: Editor, ...as: EditorAction[]) => as.reduce(r, e);
const seeded = () => run(INITIAL, { type: "server", data: SERVER, rows: [row("A", "100")] });
const setPct = (pct: string): EditorAction => ({
  type: "edit",
  patch: (rs) => rs.map((x) => ({ ...x, target_pct: pct })),
});
const load = (id: string): EditorAction => ({
  type: "load",
  id,
  spec: { kind: "fixed_weight" },
  name: `carteira ${id}`,
  desc: "",
  rows: [row("C", "100")],
});

test("the server seeds a clean live editor once per payload, never a dirty one", () => {
  const e = seeded();
  assert.equal(e.mode, "live");
  assert.deepEqual(e.rows.map((x) => x.strategy_ref), ["A"]);
  assert.ok(!needsSync(e, SERVER));
  assert.ok(needsSync(e, SERVER2));
  const dirty = r(e, setPct("50"));
  assert.ok(!needsSync(dirty, SERVER2));
  assert.equal(r(dirty, { type: "server", data: SERVER2, rows: [] }), dirty);
});

test("typing during a live save survives the save", () => {
  // The old onSuccess cleared `dirty`, so the refetch reseeded over this edit.
  const typed = r(seeded(), setPct("60"));
  const sent = typed.rows;
  const during = r(typed, setPct("70"));
  const after = r(during, { type: "liveSaved", sent });
  assert.equal(after.mode, "live");
  assert.ok(after.mode === "live" && after.dirty && !after.saved);
  assert.ok(!needsSync(after, SERVER2));
  assert.equal(after.rows[0].target_pct, "70");
});

test("a save of the rows on screen is clean, says so, and the next edit clears it", () => {
  const typed = r(seeded(), setPct("60"));
  const after = r(typed, { type: "liveSaved", sent: typed.rows });
  assert.ok(after.mode === "live" && !after.dirty && after.saved);
  assert.ok(needsSync(after, SERVER2));
  // The refetch reseeds; the message belongs to the save, so it stays…
  const reseeded = r(after, { type: "server", data: SERVER2, rows: [row("B", "100")] });
  assert.ok(reseeded.mode === "live" && reseeded.saved);
  // …until the next edit, and a later return to clean does not bring it back.
  const edited = r(reseeded, setPct("10"));
  assert.ok(edited.mode === "live" && !edited.saved);
});

test("Editar puts the live rows aside; ending restores a dirty live edit", () => {
  const live = r(seeded(), setPct("40"));
  const editing = r(live, load("x"));
  assert.equal(editing.mode, "carteira");
  assert.deepEqual(editing.rows.map((x) => x.strategy_ref), ["C"]);
  // Switching carteiras keeps the first snapshot, not the previous carteira.
  const switched = r(editing, load("y"));
  assert.ok(switched.mode === "carteira" && switched.id === "y");
  const back = r(switched, { type: "end" });
  assert.equal(back.mode, "live");
  assert.equal(back.rows, live.rows);
  assert.ok(back.mode === "live" && back.dirty);
});

test("ending over clean live rows reseeds them from the server", () => {
  const back = run(seeded(), load("x"), { type: "end" });
  assert.ok(back.mode === "live" && !back.dirty);
  assert.ok(needsSync(back, SERVER));
});

test("Excluir ends the edit of that carteira only", () => {
  const editing = run(seeded(), load("x"));
  assert.equal(r(editing, { type: "deleted", id: "other" }), editing);
  assert.equal(r(editing, { type: "deleted", id: "x" }).mode, "live");
  const live = seeded();
  assert.equal(r(live, { type: "deleted", id: "x" }), live);
});

test("a save that ends one edit does not end a later one", () => {
  const editing = run(seeded(), load("y"));
  assert.equal(r(editing, { type: "end", id: "x" }), editing);
  assert.equal(r(editing, { type: "end", id: "y" }).mode, "live");
});

test("carteira fields and edits stay in the carteira, not the live rows", () => {
  const live = seeded();
  const e = run(live, load("x"), { type: "field", name: "Nova" }, setPct("25"));
  assert.ok(e.mode === "carteira" && e.name === "Nova" && e.desc === "");
  assert.equal(e.rows[0].target_pct, "25");
  assert.equal(r(live, { type: "field", name: "ignored" }), live);
  const back = r(e, { type: "end" });
  assert.equal(back.rows[0].strategy_ref, "A");
});

test("a live save landing mid-carteira-edit settles the live rows put aside", () => {
  const typed = r(seeded(), setPct("60"));
  const e = run(typed, load("x"), { type: "liveSaved", sent: typed.rows });
  assert.ok(e.mode === "carteira" && !e.live.dirty && e.live.saved);
});
