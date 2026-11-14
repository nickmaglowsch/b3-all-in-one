// What the /bench "Minhas estratégias" card is showing: the list, or the rule
// form for a new strategy, an edit of a saved one, or a clone of a gallery one.
// One value, held by BenchPage, because "Clonar" lives in a sibling (RunPanel):
// it used to set the parent's `cloneFrom` while the card kept its own `editing`,
// and with both set the form discarded the unsaved edit, said "Clonado de X",
// and still saved over the strategy being edited.
//
// Cloning while an edit is open is refused, with the reason on screen: the edit
// may hold unsaved work, and the user can end it in one click (Cancelar).
// Replacing it would lose that work without asking.
//
// Pure, type-only imports: `npm test` runs this file through `node --test`.

import type { Strategy, UserStrategy } from "./types.ts";

export type FormMode =
  | { kind: "closed" }
  | { kind: "new" }
  | { kind: "edit"; s: UserStrategy }
  | { kind: "clone"; from: Strategy };

export const CLOSED: FormMode = { kind: "closed" };

/** Why "Clonar" cannot open the form right now, or null when it can. */
export function cloneBlocked(mode: FormMode): string | null {
  return mode.kind === "edit"
    ? `Salve ou cancele a edição de “${mode.s.name}” antes de clonar.`
    : null;
}

/** "Clonar" on `from`: opens (or re-points) the clone form, unless blocked. */
export const withClone = (mode: FormMode, from: Strategy): FormMode =>
  cloneBlocked(mode) ? mode : { kind: "clone", from };

/** The saved strategy the form writes to; a new or cloned one has none. */
export const editingOf = (mode: FormMode): UserStrategy | null =>
  mode.kind === "edit" ? mode.s : null;

export const cloneOf = (mode: FormMode): Strategy | null =>
  mode.kind === "clone" ? mode.from : null;
