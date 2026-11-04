// The macro flags the bench's regime picker offers (app/bench/RegimeEditor.tsx).
//
// Each must be a market-wide series — one value per rebalance date — on the
// engine's list, engine/backtests/core/regimes.py REGIME_FLAGS, which
// validate_spec enforces at save. A per-ticker frame does not belong here:
// `above_ma200` (dates x tickers) used to be offered, read False on every date
// and parked the book in CDI for the whole run; it stays usable in a signal
// expression. ibov_vol_pctrank is a 0..1 percentile → usually paired with `gt`;
// the rest read as booleans.
//
// Pure and import-free, because `npm test` runs this file straight under node --test.
export const REGIME_FLAGS = [
  "is_easing",
  "ibov_above",
  "ibov_calm",
  "ibov_uptrend",
  "ibov_vol_pctrank",
] as const;

/** The picker's options for an input currently reading `current`. A stored spec
 *  can name a flag off the list (above_ma200, before it was dropped); it is kept
 *  as the first option so the select shows what will be saved — and what the
 *  save then refuses — instead of silently displaying is_easing. */
export function flagOptions(current: string): readonly string[] {
  return (REGIME_FLAGS as readonly string[]).includes(current)
    ? REGIME_FLAGS
    : [current, ...REGIME_FLAGS];
}
