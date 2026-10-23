// Run: npm test (node --test, native TS — no runner dependency).
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  CASH_REF,
  fromSelect,
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

test("a strategy named like a ticker, or like the old encoding, stays a strategy", () => {
  for (const name of ["IVVB11.SA", "ticker:IVVB11.SA"]) {
    const [row] = specToRows({ sleeves: [{ strategy: name, weight: 1 }] });
    assert.equal(selectValue(row), name);
    assert.deepEqual(fromSelect(selectValue(row)), { strategy_ref: name, ticker: undefined });
    assert.deepEqual(rowToSleeve(row), { strategy_ref: name, target_pct: 100 });
  }
  // And a ticker row's select value is one no strategy option can have.
  const [tickerRow] = specToRows({ sleeves: [{ ticker: "IVVB11.SA", weight: 1 }] });
  assert.notEqual(selectValue(tickerRow), "IVVB11.SA");
  assert.deepEqual(fromSelect(selectValue(tickerRow)), { strategy_ref: "", ticker: "IVVB11.SA" });
  assert.equal(selectValue(tickerRow), tickerValue("IVVB11.SA"));
});

test("the saved tickers stay pickable after a row moves off them", () => {
  const saved = specTickers(THIRDS);
  assert.deepEqual(saved, ["DIVO11.SA"]);
  const rows = specToRows(THIRDS);
  const moved = [{ ...rows[0], ...fromSelect("MomSharpe") }, ...rows.slice(1)];
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
