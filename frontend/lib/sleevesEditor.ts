// The /estrategia sleeves editor's mode, apart from React. One set of rows, two
// owners: the live sleeves (the rebalance target) or a saved carteira borrowed by
// "Editar". This used to be eight useStates whose combinations meant the mode,
// and a few of the combinations were bugs:
//
//  - the live "Salvar" cleared `dirty` on success, before the ["sleeves"] refetch
//    landed, so the render-time reseed then overwrote whatever was typed during
//    the save. `liveSaved` compares the rows to the ones sent instead: rows typed
//    since are a new edit, and stay.
//  - "Excluir" on the carteira being edited left the edit open, and "Atualizar"
//    then PUT to a deleted id. `deleted` ends that edit.
//  - "Sleeves salvos." showed on any later return to clean. `saved` belongs to
//    one save, and the next edit clears it.
//
// Pure, type-only imports: `npm test` runs this file through `node --test`. The
// caller makes the rows' uids (withUid) before dispatching, so the reducer stays
// deterministic under StrictMode's double call.

import type { LiveSleeve, SleeveRow } from "./portfolioSleeves.ts";

type Rows = SleeveRow[];
type Synced = readonly LiveSleeve[] | null;

/** The live sleeves in the editor. `dirty` keeps the server's next payload from
 *  reseeding the rows; `syncedFrom` is the payload they were last seeded from;
 *  `saved` is true from a successful save until the next edit. */
export interface LiveEditor {
  mode: "live";
  rows: Rows;
  dirty: boolean;
  syncedFrom: Synced;
  saved: boolean;
}

/** A saved carteira in the editor. `live` is the live editor as "Editar" found
 *  it: the rows are shared, so without it a cancelled edit would leave the
 *  carteira's mix in the live editor, one "Salvar" from becoming the target. */
export interface CarteiraEditor {
  mode: "carteira";
  id: string;
  spec: Record<string, unknown>;
  name: string;
  desc: string;
  rows: Rows;
  live: LiveEditor;
}

export type Editor = LiveEditor | CarteiraEditor;

export type EditorAction =
  | { type: "server"; data: readonly LiveSleeve[]; rows: Rows }
  | { type: "edit"; patch: (rows: Rows) => Rows }
  | { type: "field"; name?: string; desc?: string }
  | { type: "load"; id: string; spec: Record<string, unknown>; name: string; desc: string; rows: Rows }
  | { type: "end"; id?: string }
  | { type: "liveSaved"; sent: Rows }
  | { type: "deleted"; id: string };

export const INITIAL: Editor = {
  mode: "live",
  rows: [],
  dirty: false,
  syncedFrom: null,
  saved: false,
};

/** Should the server's sleeves reseed the editor? Only the live editor, only
 *  when clean, once per payload (by reference). The caller checks this before
 *  dispatching `server` during render, so a render never queues a no-op. */
export const needsSync = (e: Editor, data?: Synced): data is readonly LiveSleeve[] =>
  e.mode === "live" && !e.dirty && !!data && data !== e.syncedFrom;

export const carteiraOf = (e: Editor): CarteiraEditor | null =>
  e.mode === "carteira" ? e : null;

/** The spec an Atualizar keeps the carteira's other fields from; {} for a new one. */
export const specOf = (e: Editor): Record<string, unknown> =>
  e.mode === "carteira" ? e.spec : {};

/** "Sleeves salvos." and the save's warnings: from that save to the next edit. */
export const justSaved = (e: Editor) => e.mode === "live" && e.saved;

const sync = (e: Editor, data: readonly LiveSleeve[], rows: Rows): Editor =>
  e.mode === "live" && needsSync(e, data) ? { ...e, rows, syncedFrom: data } : e;

const field = (e: Editor, a: { name?: string; desc?: string }): Editor =>
  e.mode === "carteira" ? { ...e, name: a.name ?? e.name, desc: a.desc ?? e.desc } : e;

function edit(e: Editor, patch: (rows: Rows) => Rows): Editor {
  const rows = patch(e.rows);
  return e.mode === "live" ? { ...e, rows, dirty: true, saved: false } : { ...e, rows };
}

/** The live editor as it stood when "Editar" borrowed the rows. Switching from one
 *  carteira to another keeps the first snapshot, never the previous carteira. A
 *  save message from before the edit does not come back after it. */
const liveOf = (e: Editor): LiveEditor =>
  e.mode === "live" ? { ...e, saved: false } : e.live;

function load(e: Editor, a: Extract<EditorAction, { type: "load" }>): CarteiraEditor {
  const { id, spec, name, desc, rows } = a;
  return { mode: "carteira", id, spec, name, desc, rows, live: liveOf(e) };
}

/** Hand the editor back to the live sleeves: as they were (a pending live edit
 *  survives), or — if they were clean — reseeded from the server on the next
 *  render, which may have moved since (Ativar). */
function end(e: Editor, id?: string): Editor {
  if (e.mode === "live" || (id !== undefined && id !== e.id)) return e;
  return e.live.dirty ? e.live : { ...e.live, syncedFrom: null };
}

/** The PUT of `sent` succeeded. Rows typed since are a newer edit and stay dirty;
 *  otherwise the editor is clean and the refetch reseeds it. In carteira mode the
 *  save was of the live rows put aside, so it lands on that snapshot. */
function savedLive(l: LiveEditor, sent: Rows): LiveEditor {
  const clean = l.rows === sent;
  return { ...l, dirty: !clean, saved: clean };
}

const liveSaved = (e: Editor, sent: Rows): Editor =>
  e.mode === "live" ? savedLive(e, sent) : { ...e, live: savedLive(e.live, sent) };

export function editorReducer(e: Editor, a: EditorAction): Editor {
  switch (a.type) {
    case "server":
      return sync(e, a.data, a.rows);
    case "edit":
      return edit(e, a.patch);
    case "field":
      return field(e, a);
    case "load":
      return load(e, a);
    case "end":
      return end(e, a.id);
    case "liveSaved":
      return liveSaved(e, a.sent);
    case "deleted":
      return end(e, a.id);
  }
}
