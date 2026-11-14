// Where a background job stands: an import of a B3 extract, or a backtest run.
// The two share a status column shape but not its words — imports move
// pending → processing → done | error, backtests pending → running → done | error
// (platform_app: tasks.py, worker.py). One set of predicates for both, so "is it
// still in flight" means the same thing to the bench poller, the run page and the
// imports list; the bench used to leave "processing" out, and the next status
// either side adds would have split them again.
//
// Pure and import-free, because `npm test` runs this file through `node --test`.

export type BacktestStatus = "pending" | "running" | "done" | "error";
export type ImportStatus = "pending" | "processing" | "done" | "error";
/** Either kind — what StatusBadge draws. */
export type JobStatus = BacktestStatus | ImportStatus;

// `string`, not JobStatus: these read rows straight off the API, and a status this
// build does not know is neither in flight nor finished — nothing polls for it and
// nothing renders it as a result.
type Status = string | undefined;

/** Queued or being worked on: poll it, and do not delete it (the API 409s). */
export const isBusy = (s: Status) => s === "pending" || s === "running" || s === "processing";

/** Finished with a result to show. */
export const isDone = (s: Status) => s === "done";

/** Finished either way: nothing will change it any more. */
export const isFinished = (s: Status) => s === "done" || s === "error";
