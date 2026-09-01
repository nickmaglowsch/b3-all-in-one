// Run: npm test (node --test, native TS — no runner dependency).
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  buildRunParams,
  inputAttrs,
  isSelect,
  initialParamValues,
  paramError,
  tunableParams,
} from "./strategyParams.ts";
import type { Strategy, StrategyParam } from "./types.ts";

function param(over: Partial<StrategyParam> & { name: string }): StrategyParam {
  return {
    label: over.name,
    type: "float",
    default: null,
    min: null,
    max: null,
    step: null,
    choices: null,
    ...over,
  };
}

// The real "Top Market Cap" gallery entry — the one the bug report used.
const TOP_MCAP: Strategy = {
  name: "Top Market Cap",
  kind: "code",
  description: "Holds the N largest B3 companies by point-in-time market cap.",
  needs_fundamentals: true,
  params: [
    param({ name: "start_date", label: "Start Date", type: "date", default: "2005-01-01" }),
    param({ name: "end_date", label: "End Date", type: "date", default: "today" }),
    param({
      name: "rebalance_freq", label: "Rebalance Frequency", type: "choice",
      default: "ME", choices: ["ME", "QE", "W-FRI"],
    }),
    param({ name: "top_n", label: "Number of Stocks", type: "int", default: 20, min: 1, max: 100, step: 1 }),
    param({
      name: "weighting", label: "Weighting Scheme", type: "choice",
      default: "market_cap", choices: ["market_cap", "equal"],
    }),
    param({ name: "min_adtv", label: "Min Median Volume (BRL)", type: "float", default: 2e6, min: 0, step: 1e5 }),
    param({ name: "db_path", label: "Database Path", type: "str", default: "b3_market_data.sqlite" }),
  ],
};

// ── tunableParams: what the form should render ───────────────────────────────

test("the strategy's own knobs are offered — top_n was the reported gap", () => {
  const names = tunableParams(TOP_MCAP).map((p) => p.name);
  assert.deepEqual(names, ["top_n", "weighting", "min_adtv"]);
});

test("params with a dedicated control are not rendered twice", () => {
  // start/end/frequency have their own inputs; a second copy would let the two
  // disagree, and the engine would take whichever the request happened to put last.
  const names = tunableParams(TOP_MCAP).map((p) => p.name);
  for (const owned of ["start_date", "end_date", "rebalance_freq"]) {
    assert.ok(!names.includes(owned), `${owned} must stay with its own field`);
  }
});

test("db_path is never exposed", () => {
  // A server-side filesystem path is not a user knob: it is ignored entirely
  // once ENGINE_DATABASE_URL routes the engine at Postgres, and typing into it
  // could only ever break a run.
  assert.ok(!tunableParams(TOP_MCAP).some((p) => p.name === "db_path"));
});

test("declaration order is preserved", () => {
  const spread = { ...TOP_MCAP, params: [...TOP_MCAP.params].reverse() };
  assert.deepEqual(
    tunableParams(spread).map((p) => p.name),
    ["min_adtv", "weighting", "top_n"],
  );
});

test("a strategy with nothing extra yields an empty list, not a crash", () => {
  assert.deepEqual(tunableParams({ ...TOP_MCAP, params: [] }), []);
});

// ── initialParamValues: the form starts on the engine's own defaults ─────────

test("fields start filled with the engine defaults", () => {
  assert.deepEqual(initialParamValues(tunableParams(TOP_MCAP)), {
    top_n: "20",
    weighting: "market_cap",
    min_adtv: "2000000",
  });
});

test("a null default becomes an empty field", () => {
  assert.deepEqual(initialParamValues([param({ name: "tilt", default: null })]), { tilt: "" });
});

// ── paramError: per-field validation, in pt-BR like the rest of the UI ───────

test("a value inside the declared bounds is accepted", () => {
  const top_n = tunableParams(TOP_MCAP)[0];
  assert.equal(paramError(top_n, "30"), null);
  assert.equal(paramError(top_n, "1"), null);
  assert.equal(paramError(top_n, "100"), null);
});

test("out-of-range values are refused with the bound in the message", () => {
  const top_n = tunableParams(TOP_MCAP)[0];
  assert.match(paramError(top_n, "0") ?? "", /1/);
  assert.match(paramError(top_n, "101") ?? "", /100/);
});

test("a non-numeric entry in a numeric field is refused", () => {
  assert.match(paramError(param({ name: "top_n", type: "int" }), "vinte") ?? "", /número/i);
});

test("an int field refuses a fractional value", () => {
  // top_n=2.5 reaches int() in the engine and dies mid-run; catch it in the form.
  assert.match(paramError(param({ name: "top_n", type: "int" }), "2.5") ?? "", /inteiro/i);
});

test("an empty field is valid — it means 'use the default'", () => {
  assert.equal(paramError(param({ name: "top_n", type: "int", min: 1 }), ""), null);
});

test("a choice outside the declared set is refused", () => {
  const weighting = param({ name: "weighting", type: "choice", choices: ["market_cap", "equal"] });
  assert.equal(paramError(weighting, "equal"), null);
  assert.ok(paramError(weighting, "cap_weighted"));
});

// ── buildRunParams: what actually reaches POST /backtests ────────────────────

const TUNABLE = tunableParams(TOP_MCAP);
const DEFAULTS = initialParamValues(TUNABLE);

test("an untouched form sends no params at all", () => {
  // The engine already applies its own defaults; echoing them back would only
  // pin today's values into a stored run row that outlives them.
  assert.equal(buildRunParams(TUNABLE, DEFAULTS, ""), undefined);
});

test("an edited knob is sent, typed, and alone", () => {
  const out = buildRunParams(TUNABLE, { ...DEFAULTS, top_n: "30" }, "");
  assert.deepEqual(out, { top_n: 30 });
});

test("floats and choices keep their own types", () => {
  const out = buildRunParams(
    TUNABLE, { ...DEFAULTS, min_adtv: "5e5", weighting: "equal" }, "",
  );
  assert.deepEqual(out, { min_adtv: 500000, weighting: "equal" });
});

test("a cleared field falls back to the engine default", () => {
  assert.equal(buildRunParams(TUNABLE, { ...DEFAULTS, top_n: "" }, ""), undefined);
});

test("the aporte rides along with the strategy knobs", () => {
  const out = buildRunParams(TUNABLE, { ...DEFAULTS, top_n: "5" }, "300");
  assert.deepEqual(out, { top_n: 5, contribution: 300 });
});

test("a zero or blank aporte is not sent", () => {
  assert.equal(buildRunParams(TUNABLE, DEFAULTS, "0"), undefined);
  assert.equal(buildRunParams(TUNABLE, DEFAULTS, ""), undefined);
});

test("an invalid value never reaches the request", () => {
  // run() blocks on paramError first; this is the second line of defence.
  assert.equal(buildRunParams(TUNABLE, { ...DEFAULTS, top_n: "abc" }, ""), undefined);
});

// ── control shape: how each declared type is rendered ────────────────────────

test("a param with choices renders as a select", () => {
  assert.equal(isSelect(param({ name: "weighting", type: "choice", choices: ["a", "b"] })), true);
});

test("a choice param with an empty choice list falls back to a plain input", () => {
  // Defensive: the spec allows choices: null, and a select with no options is
  // an unusable control that cannot even show its own default.
  assert.equal(isSelect(param({ name: "weighting", type: "choice", choices: [] })), false);
  assert.equal(isSelect(param({ name: "weighting", type: "choice", choices: null })), false);
});

test("numeric params carry the spec's own bounds onto the input", () => {
  const top_n = param({ name: "top_n", type: "int", min: 1, max: 100, step: 1 });
  assert.deepEqual(inputAttrs(top_n), {
    type: "number", inputMode: "numeric", min: 1, max: 100, step: 1,
  });
});

test("an unbounded float carries no bounds rather than zeros", () => {
  // min: 0 and min: null are different claims; sending undefined keeps the
  // browser from refusing a legitimate negative value on an unbounded knob.
  assert.deepEqual(inputAttrs(param({ name: "tilt", type: "float" })), {
    type: "number", inputMode: undefined, min: undefined, max: undefined, step: undefined,
  });
});

test("a non-numeric param renders as text with no numeric attributes", () => {
  assert.deepEqual(inputAttrs(param({ name: "sectors", type: "str" })), { type: "text" });
});
