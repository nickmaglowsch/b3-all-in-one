// Run: npm test (node --test, native TS — no runner dependency).
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  CASH_REF,
  fromSelect,
  pickerRefs,
  portfolioBody,
  rowToSleeve,
  selectValue,
  specTickers,
  specToRows,
  tickerOptions,
  tickerValue,
} from "./portfolioSleeves.ts";

// The spec the platform stores for a user copy of "DIVO11 / CDI / IVVB11 (thirds)".
const THIRDS = {
  kind: "fixed_weight",
  sleeves: [
    { ticker: "DIVO11.SA", weight: 0.34 },
    { asset: "CDI", weight: 0.33 },
    { strategy: "MomSharpe", weight: 0.33 },
  ],
};

test("a saved portfolio's sleeves round-trip through the editor rows", () => {
  const rows = specToRows(THIRDS);
  assert.deepEqual(rows, [
    { strategy_ref: "", ticker: "DIVO11.SA", target_pct: "34" },
    { strategy_ref: CASH_REF, target_pct: "33" },
    { strategy_ref: "MomSharpe", target_pct: "33" },
  ]);
  // A ticker goes back as a ticker — not as a strategy called "DIVO11.SA",
  // which the save would refuse as an unknown strategy_ref.
  assert.deepEqual(rows.map(rowToSleeve), [
    { ticker: "DIVO11.SA", target_pct: 34 },
    { strategy_ref: "CASH_CDI", target_pct: 33 },
    { strategy_ref: "MomSharpe", target_pct: 33 },
  ]);
});

// What a <select> onChange hands over: the value and the chosen option.
const pick = (value: string, ticker?: string) => ({
  value,
  selectedOptions: [{ dataset: ticker ? { ticker } : {} }],
});

test("a strategy named like a ticker, or like the old encoding, stays a strategy", () => {
  for (const name of ["IVVB11.SA", "ticker:IVVB11.SA"]) {
    const [row] = specToRows({ sleeves: [{ strategy: name, weight: 1 }] });
    assert.equal(selectValue(row), name);
    assert.deepEqual(fromSelect(pick(selectValue(row))), { strategy_ref: name, ticker: undefined });
    assert.deepEqual(rowToSleeve(row), { strategy_ref: name, target_pct: 100 });
  }
  // A ticker is told apart by its option's data-ticker, not by parsing a value.
  const [tickerRow] = specToRows({ sleeves: [{ ticker: "IVVB11.SA", weight: 1 }] });
  assert.equal(selectValue(tickerRow), tickerValue("IVVB11.SA"));
  assert.match(selectValue(tickerRow), /^[\x20-\x7e]+$/); // printable, survives SSR
  assert.deepEqual(fromSelect(pick(selectValue(tickerRow), "IVVB11.SA")), {
    strategy_ref: "",
    ticker: "IVVB11.SA",
  });
  // Without a selected option (a bare value), it is read as a strategy.
  assert.deepEqual(fromSelect({ value: "MomSharpe" }), { strategy_ref: "MomSharpe", ticker: undefined });
});

test("the saved tickers stay pickable after a row moves off them", () => {
  const saved = specTickers(THIRDS);
  assert.deepEqual(saved, ["DIVO11.SA"]);
  const rows = specToRows(THIRDS);
  const moved = [{ ...rows[0], ...fromSelect(pick("MomSharpe")) }, ...rows.slice(1)];
  assert.equal(moved[0].ticker, undefined);
  assert.deepEqual(tickerOptions(moved, saved), ["DIVO11.SA"]);
  // Each once, saved first.
  assert.deepEqual(tickerOptions(rows, saved), ["DIVO11.SA"]);
  assert.deepEqual(tickerOptions([], []), []);
});

test("a spec without sleeves reads as no rows", () => {
  assert.deepEqual(specToRows({}), []);
  assert.deepEqual(specTickers({}), []);
  assert.deepEqual(specToRows({ sleeves: [{ weight: 1 }] }), [
    { strategy_ref: "", target_pct: "100" },
  ]);
});

test("portfolioBody keeps the edited spec's grid, and a new carteira has none", () => {
  const rows = [{ strategy_ref: CASH_REF, target_pct: "100" }];
  const body = portfolioBody(" Semi ", "", rows, { rebalance: "ME", rebalance_months: [6, 12] });
  assert.equal(body.name, "Semi");
  assert.equal(body.rebalance, "ME");
  assert.deepEqual(body.rebalance_months, [6, 12]);
  assert.equal("rebalance_months" in portfolioBody("New", "", rows, {}), false);
});

test("the sleeve picker offers B3 strategies only, and never a user portfolio", () => {
  const gallery = [
    { name: "MomSharpe", source: "builtin", market: "B3" },
    { name: "Legacy", source: "builtin" }, // no market: the B3 default
    { name: "CryptoBTCRegime", source: "builtin", market: "CRYPTO" },
    { name: "USLowVol", source: "builtin", market: "US" },
    { name: "My momentum", source: "user", market: "B3" },
    { name: "BTC tilt", source: "user", market: "CRYPTO" },
    { name: "My blend", source: "user" },
  ];
  assert.deepEqual(pickerRefs(gallery, new Set(["My blend"])), {
    builtinRefs: ["MomSharpe", "Legacy"],
    userRefs: ["My momentum"],
  });
});
