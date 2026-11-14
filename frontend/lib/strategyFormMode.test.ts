// Run: npm test (node --test, native TS — no runner dependency).
import { test } from "node:test";
import assert from "node:assert/strict";
import type { Strategy, UserStrategy } from "./types.ts";
import {
  CLOSED,
  cloneBlocked,
  cloneOf,
  editingOf,
  withClone,
  type FormMode,
} from "./strategyFormMode.ts";

const gallery = (name: string) => ({ name }) as Strategy;
const saved = { id: "u1", name: "Meu momentum" } as UserStrategy;

test("Clonar opens the clone form from the list, a new form, or another clone", () => {
  for (const m of [CLOSED, { kind: "new" }, { kind: "clone", from: gallery("A") }] as FormMode[]) {
    const next = withClone(m, gallery("B"));
    assert.equal(cloneOf(next)?.name, "B");
    assert.equal(editingOf(next), null);
    assert.equal(cloneBlocked(m), null);
  }
});

test("Clonar during an edit is refused, says why, and keeps the edit", () => {
  // Before, the form showed "Clonado de B" and still saved to the edited id.
  const editing: FormMode = { kind: "edit", s: saved };
  const next = withClone(editing, gallery("B"));
  assert.equal(next, editing);
  assert.equal(editingOf(next), saved);
  assert.equal(cloneOf(next), null);
  assert.match(cloneBlocked(editing) ?? "", /Meu momentum/);
});
