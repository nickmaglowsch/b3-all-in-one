// An inline "Excluir → Confirmar / Não" on a list of rows, apart from React. One
// row armed at a time (two open confirms would be two rows claiming the next
// click), and the row being deleted is the mutation's own `variables` — not the
// armed id — the way /estrategia's carteiraStatus reads its mutations.
//
// Reading the busy row off the armed id was the bug this replaces: while run A
// was deleting, Excluir on run B moved the armed id to B, so B showed as the one
// deleting, and A's onSettled then closed B's confirm.
//
// Pure and import-free, because `npm test` runs this file through `node --test`.

export type RowState = "idle" | "armed" | "busy";

/** `busyId` is the id a pending delete was called with, null when none is. */
export function rowState(id: string, armed: string | null, busyId: string | null): RowState {
  if (busyId === id) return "busy";
  return armed === id ? "armed" : "idle";
}

/** Arming is ignored while a delete is in flight: the armed row is that one. */
export const arm = (armed: string | null, id: string, busyId: string | null) =>
  busyId === null ? id : armed;

/** A delete of `id` settled: it closes its own confirm, never another row's. */
export const settle = (armed: string | null, id: string) => (armed === id ? null : armed);
