// What the bench's "limpar rodadas" control sends to DELETE /backtests.
//
// Kept out of the component because it is the part with rules: the API refuses a
// sweep with no criteria at all (a bare DELETE /backtests reads as "clear the
// list" to whoever clicked and as "delete every backtest I have ever run" to the
// database), and the cutoff is a *calendar* date the user's own day decides, not
// a UTC instant. Both are checkable here and unreachable inside JSX.

/** One sweep's criteria, as the control offers them. */
export interface PurgeChoice {
  /** Minimum age in days. null = any age, i.e. age is not a criterion. */
  olderThanDays: number | null;
  /** Only the runs that ended in `error`. */
  onlyErrors: boolean;
}

/** The ages offered. A day is missing on purpose: "older than today" is the
 *  request that deletes the run someone is still reading. */
export const PURGE_WINDOWS = [
  { days: 7, label: "7 dias" },
  { days: 30, label: "30 dias" },
  { days: 90, label: "90 dias" },
] as const;

/**
 * `days` ago as yyyy-mm-dd in the *viewer's* timezone.
 *
 * Not `toISOString().slice(0, 10)`: at 21:00 in São Paulo that is already
 * tomorrow in UTC, so a "30 days" sweep would silently cut at 29. The API reads
 * this as UTC midnight, which is the boundary the user pointed at ±3h — close
 * enough for a retention window, and honest about which day it means.
 */
export function isoDaysAgo(days: number, from: Date = new Date()): string {
  const d = new Date(from.getFullYear(), from.getMonth(), from.getDate() - days);
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${mm}-${dd}`;
}

/**
 * Query string for DELETE /backtests, or null when the choice names no
 * criterion — which the API answers 422 and the button should not offer.
 */
export function purgeQuery(choice: PurgeChoice, from: Date = new Date()): string | null {
  const parts: string[] = [];
  if (choice.olderThanDays !== null) {
    parts.push(`before=${isoDaysAgo(choice.olderThanDays, from)}`);
  }
  if (choice.onlyErrors) parts.push("status=error");
  return parts.length ? parts.join("&") : null;
}

/** The criteria in words, for the confirm button. The count is deliberately not
 *  in it: the page holds one capped listing and the sweep reaches every row past
 *  it, so any number shown here would be the wrong one. */
export function purgeLabel(choice: PurgeChoice): string {
  const what = choice.onlyErrors ? "rodadas com erro" : "rodadas concluídas";
  return choice.olderThanDays === null
    ? `Excluir todas as ${what}`
    : `Excluir ${what} com mais de ${choice.olderThanDays} dias`;
}
