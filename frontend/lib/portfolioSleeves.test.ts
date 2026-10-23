// Run: npm test (node --test, native TS — no runner dependency).
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  CASH_REF,
  rowToSleeve,
  specToRows,
  tickerLabel,
  tickerRefs,
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
    { strategy_ref: "ticker:DIVO11.SA", target_pct: "34" },
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

test("the picker gets each ticker once, labelled without the prefix", () => {
  const rows = [
    ...specToRows(THIRDS),
    { strategy_ref: "ticker:DIVO11.SA", target_pct: "0" },
  ];
  assert.deepEqual(tickerRefs(rows), ["ticker:DIVO11.SA"]);
  assert.equal(tickerLabel("ticker:DIVO11.SA"), "DIVO11.SA");
  assert.deepEqual(tickerRefs(specToRows({ sleeves: [] })), []);
});

test("a spec without sleeves reads as no rows", () => {
  assert.deepEqual(specToRows({}), []);
  assert.deepEqual(specToRows({ sleeves: [{ weight: 1 }] }), [
    { strategy_ref: "", target_pct: "100" },
  ]);
});
