// What an import that just finished makes stale. The book's positions, trades,
// income, tax and index are all rebuilt from the extracts, and every one of
// them sits in React Query's cache for 30s with no refetch on focus — so a user
// who looked at an empty /carteira, imported, and came back inside that window
// kept being told nothing had been imported.
//
// An exclusion list rather than an inclusion one: a new book-scoped query is
// refreshed by default, which is the safe way for this to be wrong.
//
// Pure and import-free, because `npm test` runs this file through `node --test`.

/** Query keys (their first segment) whose data does not come from the extracts:
 *  the login, the catalogues baked into the image, backtests of the platform's
 *  own strategies, and the imports list itself (already being polled). */
const NOT_FROM_IMPORTS = new Set([
  "me",
  "portfolios",
  "subscription",
  "health",
  "market-status",
  "strategies",
  "signals",
  "benchmarks",
  "benchmark-series",
  "strategy-spec",
  "user-strategies",
  "backtests",
  "backtest",
  "curves",
  "imports",
]);

export function dependsOnImports(queryKey: readonly unknown[]): boolean {
  return !NOT_FROM_IMPORTS.has(String(queryKey[0]));
}

export const isBusyImport = (status: string) =>
  status === "pending" || status === "processing" || status === "running";

/** Each import's id → whether it was still being processed. */
export type ImportSnapshot = Map<string, boolean>;

export function snapshotImports(rows: { id: string; status: string }[]): ImportSnapshot {
  return new Map(rows.map((r) => [r.id, isBusyImport(r.status)]));
}

/**
 * Whether some import finished between `prev` and `rows`: one that was busy and
 * no longer is, or one that showed up already finished (a small file can be
 * processed before the first poll ever sees it running). With no previous
 * snapshot — the page's first load — nothing has changed *since* anything.
 */
export function importsSettledSince(
  prev: ImportSnapshot | null,
  rows: { id: string; status: string }[],
): boolean {
  if (prev === null) return false;
  return rows.some((r) => !isBusyImport(r.status) && prev.get(r.id) !== false);
}
