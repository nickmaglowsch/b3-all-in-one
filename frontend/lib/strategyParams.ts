// The knobs a strategy declares, turned into run-form state.
//
// GET /strategies already ships every strategy's full ParameterSpec list
// (name/label/type/default/min/max/step/choices — see engine
// scripts/list_strategies.py), but the bench run form only ever rendered
// period, frequency and aporte. Everything else — top_n, weighting, min_adtv,
// the lot — was reachable only by hand-posting to /backtests. These helpers are
// the whole decision layer, kept pure so `npm test` can cover it: RunPanel just
// renders what tunableParams returns and posts what buildRunParams builds.
import type { Strategy, StrategyParam } from "./types";

// Rendered by their own dedicated control. `start_date`/`end_date`/
// `rebalance_freq` map onto the request's top-level start/end/freq, and
// `contribution` onto the aporte field; a second input for any of them would
// let the two disagree with no way for the user to tell which one won.
const FORM_OWNED = new Set(["start_date", "end_date", "rebalance_freq", "contribution"]);

// Not user knobs. db_path is a server-side filesystem path that the engine
// ignores outright once ENGINE_DATABASE_URL points it at Postgres (see
// b3_pipeline/storage.py get_connection) — editable only to break a run.
const INTERNAL = new Set(["db_path"]);

const NUMERIC = new Set(["int", "float"]);

/** The strategy-specific knobs the run form should render, in declared order. */
export function tunableParams(strategy: Strategy): StrategyParam[] {
  return (strategy.params ?? []).filter(
    (p) => !FORM_OWNED.has(p.name) && !INTERNAL.has(p.name),
  );
}

/** Text form of a param's default — what an untouched field shows. */
function defaultText(p: StrategyParam): string {
  return p.default === null || p.default === undefined ? "" : String(p.default);
}

/** Form state for a param list: every field pre-filled with the engine default. */
export function initialParamValues(params: StrategyParam[]): Record<string, string> {
  return Object.fromEntries(params.map((p) => [p.name, defaultText(p)]));
}

/**
 * Why `raw` is not a valid value for `p`, or null when it is.
 *
 * Empty is always valid and means "leave it to the engine". Bounds come from
 * the ParameterSpec, so the message quotes the strategy's own limit rather than
 * a number this file invented.
 */
export function paramError(p: StrategyParam, raw: string): string | null {
  const value = raw.trim();
  if (value === "") return null;
  if (p.type === "choice") return choiceError(p, value);
  if (!NUMERIC.has(p.type)) return null;
  return numberError(p, value);
}

function choiceError(p: StrategyParam, value: string): string | null {
  const choices = (p.choices ?? []).map(String);
  if (choices.length === 0) return null;
  return choices.includes(value)
    ? null
    : `Escolha um valor da lista: ${choices.join(", ")}.`;
}

function numberError(p: StrategyParam, value: string): string | null {
  const n = Number(value);
  if (!Number.isFinite(n)) return "Informe um número.";
  // Before the bounds: 2.5 sits inside top_n's 1..100 and still dies at int()
  // inside the engine, mid-run, with the queue slot already spent.
  if (p.type === "int" && !Number.isInteger(n)) return "Informe um número inteiro.";
  if (p.min !== null && n < p.min) return `Mínimo ${p.min}.`;
  if (p.max !== null && n > p.max) return `Máximo ${p.max}.`;
  return null;
}

/**
 * The `params` body for POST /backtests — or undefined when there is nothing
 * to say.
 *
 * Only values the user actually changed are sent. The engine merges the request
 * over `get_default_parameters()` anyway (scripts/run_backtest_json.py), so
 * echoing the defaults back would pin today's values into a run row that
 * outlives them, and make every stored run look hand-tuned.
 */
export function buildRunParams(
  params: StrategyParam[],
  values: Record<string, string>,
  aporte: string,
): Record<string, number | string> | undefined {
  const out: Record<string, number | string> = {};

  for (const p of params) {
    const edited = editedValue(p, values[p.name] ?? "");
    if (edited !== undefined) out[p.name] = edited;
  }

  const contribution = contributionOf(aporte);
  if (contribution !== undefined) out.contribution = contribution;

  return Object.keys(out).length ? out : undefined;
}

/** A param's value when the user changed it to something valid, else undefined. */
function editedValue(
  p: StrategyParam,
  raw: string,
): number | string | undefined {
  const value = raw.trim();
  if (value === "" || value === defaultText(p)) return undefined;
  if (paramError(p, value)) return undefined;   // run() blocks first; belt and braces
  return NUMERIC.has(p.type) ? Number(value) : value;
}

/** The aporte as a number, or undefined when there is no contribution to make. */
function contributionOf(aporte: string): number | undefined {
  const n = Number(aporte);
  return aporte.trim() !== "" && Number.isFinite(n) && n > 0 ? n : undefined;
}

/** True when the spec enumerates the allowed values, i.e. render a <select>. */
export function isSelect(p: StrategyParam): boolean {
  return (p.choices?.length ?? 0) > 0;
}

/**
 * The <input> attributes for a param, so the browser's own stepper and
 * validation follow the strategy's declared bounds instead of guessing.
 * An absent bound stays `undefined`: min 0 and "no minimum" are different
 * claims, and collapsing them would refuse legitimate negative values on
 * knobs like `tilt`.
 */
export function inputAttrs(p: StrategyParam): {
  type: "number" | "text";
  inputMode?: "numeric";
  min?: number;
  max?: number;
  step?: number;
} {
  if (!NUMERIC.has(p.type)) return { type: "text" };
  return {
    type: "number",
    inputMode: p.type === "int" ? "numeric" : undefined,
    min: p.min ?? undefined,
    max: p.max ?? undefined,
    step: p.step ?? undefined,
  };
}
