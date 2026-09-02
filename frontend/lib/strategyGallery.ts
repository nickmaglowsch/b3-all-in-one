// What the gallery list shows and what its search box matches.
//
// Kept here rather than inline in StrategyGallery.tsx for the same reason
// strategyParams.ts is: `npm test` runs `lib/**/*.test.ts` and nothing renders
// components, so a decision that lives in a component is a decision nothing
// covers. The component renders what these return.
import type { Strategy } from "./types";

// The plane every strategy ran on before the US and crypto planes landed, and
// still the default for any spec that does not name one (engine
// backtests/core/markets.py DEFAULT).
export const DEFAULT_MARKET = "B3";

/**
 * Whether to badge this row with its market. B3 is 41 of the 53 built-ins, so a
 * chip on every row carries no information — the two planes that are not the
 * default do.
 */
export function marketBadge(s: Strategy): string | null {
  const market = s.market;
  if (!market || market === DEFAULT_MARKET) return null;
  return market.toLowerCase();
}

/**
 * Search predicate for the gallery box. Matches name, description and market,
 * case-insensitively. The market is on the row as a badge rather than in the
 * prose, so without it here "crypto" finds only the strategies that happen to
 * spell the word in their description.
 */
export function matchesQuery(s: Strategy, needle: string): boolean {
  const q = needle.trim().toLowerCase();
  if (!q) return true;
  return (
    s.name.toLowerCase().includes(q) ||
    s.description.toLowerCase().includes(q) ||
    (s.market ?? "").toLowerCase().includes(q)
  );
}
