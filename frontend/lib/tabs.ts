// Which tab a page opens on. The tab lives in the URL (`?aba=`) so a view is
// linkable and survives a reload — but the URL is input, so a stale or typed
// value falls back to the first tab instead of rendering a page with nothing
// selected. Pure and import-free for `node --test`.

export const TAB_PARAM = "aba";

/** `requested` when it names one of `ids`, else the first id. */
export function pickTab(ids: readonly string[], requested: string | null | undefined): string {
  return requested && ids.includes(requested) ? requested : (ids[0] ?? "");
}

/** The id `step` places away from `current`, wrapping at both ends — the
 *  arrow-key move of a WAI-ARIA tablist. */
export function stepTab(ids: readonly string[], current: string, step: number): string {
  if (ids.length === 0) return current;
  const i = Math.max(0, ids.indexOf(current));
  return ids[(i + step + ids.length) % ids.length];
}
