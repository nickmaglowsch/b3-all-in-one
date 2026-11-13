// A searchable combobox's behaviour, apart from React: what a query matches,
// which option the keyboard lands on, and what Enter / Escape mean. The
// component (components/ui/Combobox.tsx) only renders this and wires events —
// the ARIA "combobox with listbox popup" pattern, with aria-activedescendant,
// so focus stays in the text field while the arrows move through the list.
//
// Pure and import-free, because `npm test` runs this file through `node --test`.

export interface ComboOption {
  id: string; // unique within the list; also the DOM id suffix
  label: string;
  group: string;
  detail?: string;
  // Set when the option is shown but cannot be chosen; says why.
  disabledReason?: string;
}

export interface ComboState {
  open: boolean;
  active: number; // index into the options, -1 for none
}

export const CLOSED: ComboState = { open: false, active: -1 };

/** Lower case, accents folded: "Ação" is found by "acao". */
export function fold(text: string): string {
  return text.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
}

/** Every word of the query appears in `text`, in any order. Empty matches all. */
export function matches(query: string, text: string): boolean {
  const hay = fold(text);
  return fold(query)
    .split(/\s+/)
    .filter(Boolean)
    .every((w) => hay.includes(w));
}

const enabled = (o: ComboOption | undefined) => !!o && !o.disabledReason;

/** The next enabled index from `from` in `dir` (+1 / -1), or `from` when there
 *  is none — the active option stays put at either end rather than wrapping. */
export function step(options: readonly ComboOption[], from: number, dir: 1 | -1): number {
  for (let i = from + dir; i >= 0 && i < options.length; i += dir) {
    if (enabled(options[i])) return i;
  }
  return from;
}

// -1 when no option can be chosen.
export const firstEnabled = (options: readonly ComboOption[]) => step(options, -1, 1);
export function lastEnabled(options: readonly ComboOption[]): number {
  const i = step(options, options.length, -1);
  return i === options.length ? -1 : i;
}

export interface KeyResult {
  state: ComboState;
  select?: number; // the option Enter chose
  clear?: boolean; // Escape on a closed list: empty the query
  handled: boolean; // the caller should preventDefault
}

type KeyHandler = (state: ComboState, options: readonly ComboOption[]) => KeyResult;

const openAt = (active: number): KeyResult => ({ state: { open: true, active }, handled: true });
const ignore = (state: ComboState): KeyResult => ({ state, handled: false });

// The keys the pattern gives the field; every other key is typing.
const KEYS = new Map<string, KeyHandler>([
  ["ArrowDown", (s, o) => openAt(s.open ? step(o, s.active, 1) : firstEnabled(o))],
  ["ArrowUp", (s, o) => openAt(s.open ? step(o, s.active, -1) : lastEnabled(o))],
  // Closed, Home/End move the caret in the text field, as they should.
  ["Home", (s, o) => (s.open ? openAt(firstEnabled(o)) : ignore(s))],
  ["End", (s, o) => (s.open ? openAt(lastEnabled(o)) : ignore(s))],
  // Closed, Enter is the form's, not the list's; on a disabled option, nothing.
  ["Enter", (s, o) =>
    s.open && enabled(o[s.active])
      ? { state: CLOSED, select: s.active, handled: true }
      : { state: s, handled: s.open }],
  ["Escape", (s) =>
    s.open ? { state: CLOSED, handled: true } : { state: s, clear: true, handled: true }],
  // Tab leaves the field: close, but let the focus move.
  ["Tab", () => ignore(CLOSED)],
]);

/** One keydown, as the WAI-ARIA combobox pattern describes it. */
export function onKey(
  state: ComboState,
  key: string,
  options: readonly ComboOption[],
): KeyResult {
  return (KEYS.get(key) ?? ignore)(state, options);
}

/** What the browser says an edit was (InputEvent.inputType / .data). */
export interface EditInfo {
  inputType?: string;
  data?: string | null;
}

/**
 * The query an edit of the field means. Open, the field holds the query itself.
 * Closed, it shows the current choice ("PETR4"), so a keystroke there must start
 * a fresh search for what was typed — "V", not "PETR4V".
 *
 * The edit's own account decides, because the text alone cannot: "P" is both
 * "PETR4 selected whole and typed over" (focus by Tab selects it) and "PETR4
 * with 'ETR4' deleted". Typed or composed text is `data`; any deletion clears
 * the search. Without that account (a paste, an old browser), what replaced the
 * choice is the query: the inserted part when the choice is still there whole,
 * else the field's whole new text.
 */
export function typedQuery(open: boolean, shown: string, next: string, edit: EditInfo = {}): string {
  if (open || !shown) return next;
  const type = edit.inputType ?? "";
  if (type.startsWith("delete")) return "";
  if (type.startsWith("insert") && typeof edit.data === "string" && edit.data) return edit.data;
  return next.includes(shown) ? inserted(shown, next) : next;
}

/** What an insertion into `shown` added: `next` less the prefix and suffix the
 *  two share (bounded, so "AAPL" → "AAPLA" is "A", not ""). */
function inserted(shown: string, next: string): string {
  let head = 0;
  while (head < shown.length && shown[head] === next[head]) head++;
  let tail = 0;
  while (tail < shown.length - head && shown[shown.length - 1 - tail] === next[next.length - 1 - tail]) {
    tail++;
  }
  return next.slice(head, next.length - tail);
}

/** The active option after the list changed under it (a new query, new search
 *  results): kept when it is still there, else the first enabled one — unless
 *  the list is not `settled` yet (results for an older query still showing):
 *  then none, so Enter cannot choose what the user did not ask for. */
export function reconcile(
  state: ComboState,
  before: readonly ComboOption[],
  after: readonly ComboOption[],
  settled = true,
): ComboState {
  if (!state.open) return state;
  const id = before[state.active]?.id;
  const kept = id === undefined ? -1 : after.findIndex((o) => o.id === id);
  if (enabled(after[kept])) return { open: true, active: kept };
  return { open: true, active: settled ? firstEnabled(after) : -1 };
}

/** Consecutive options of one group, in order, with their list index. */
export function grouped(
  options: readonly ComboOption[],
): { group: string; items: { option: ComboOption; index: number }[] }[] {
  const out: { group: string; items: { option: ComboOption; index: number }[] }[] = [];
  options.forEach((option, index) => {
    const last = out[out.length - 1];
    if (last && last.group === option.group) last.items.push({ option, index });
    else out.push({ group: option.group, items: [{ option, index }] });
  });
  return out;
}
