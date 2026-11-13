// Run: npm test (node --test, native TS — no runner dependency).
import { test } from "node:test";
import assert from "node:assert/strict";
import { onKey, reconcile } from "./combobox.ts";
import {
  CASH_REF,
  heldAssets,
  liveToRows,
  offB3Rows,
  pickerOptions,
  pickerRefs,
  portfolioBody,
  rowKey,
  rowLabel,
  rowToSleeve,
  specTickers,
  specToRows,
  withUid,
  type AssetHit,
  type PickerInput,
  type SleeveRow,
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
    { strategy_ref: "", ticker: "DIVO11.SA", market: "B3", target_pct: "34" },
    { strategy_ref: CASH_REF, target_pct: "33" },
    { strategy_ref: "MomSharpe", target_pct: "33" },
  ]);
  // A ticker goes back as a ticker — not as a strategy called "DIVO11.SA",
  // which the save would refuse as an unknown strategy_ref — and a B3 one in
  // the shape the API and MCP have always taken.
  assert.deepEqual(rows.map(rowToSleeve), [
    { ticker: "DIVO11.SA", target_pct: 34 },
    { strategy_ref: "CASH_CDI", target_pct: 33 },
    { strategy_ref: "MomSharpe", target_pct: 33 },
  ]);
});

test("the live sleeves round-trip with their market", () => {
  const live = [
    { strategy_ref: "CASH_CDI", target_pct: 20 },
    { ticker: "PETR4", market: "B3" as const, target_pct: 30 },
    { ticker: "SPY", market: "US" as const, target_pct: 30 },
    { ticker: "BTCUSDT", market: "CRYPTO" as const, target_pct: 20 },
  ];
  const rows = liveToRows(live);
  assert.deepEqual(rows[2], { strategy_ref: "", ticker: "SPY", market: "US", target_pct: "30" });
  assert.deepEqual(rows.map(rowToSleeve), [
    { strategy_ref: "CASH_CDI", target_pct: 20 },
    { ticker: "PETR4", target_pct: 30 },
    { ticker: "SPY", market: "US", target_pct: 30 },
    { ticker: "BTCUSDT", market: "CRYPTO", target_pct: 20 },
  ]);
  assert.deepEqual(rows.map(rowLabel), ["Caixa (CDI)", "PETR4", "SPY · EUA", "BTCUSDT · cripto"]);
});

test("a row's key tells assets from strategies and spellings of one asset apart", () => {
  const row = (r: Partial<SleeveRow>): SleeveRow => ({ strategy_ref: "", target_pct: "", ...r });
  // A strategy named like a ticker, or like a key, stays a strategy.
  for (const name of ["IVVB11.SA", "ticker:IVVB11.SA"]) {
    const [r] = specToRows({ sleeves: [{ strategy: name, weight: 1 }] });
    assert.equal(rowKey(r), name);
    assert.deepEqual(rowToSleeve(r), { strategy_ref: name, target_pct: 100 });
  }
  // IVVB11.SA from a saved carteira and IVVB11 from the search are one asset…
  assert.equal(rowKey(row({ ticker: "IVVB11.SA", market: "B3" })), rowKey(row({ ticker: "ivvb11" })));
  // …but the same letters on two markets are two.
  assert.notEqual(rowKey(row({ ticker: "SPY", market: "US" })), rowKey(row({ ticker: "SPY", market: "CRYPTO" })));
  assert.match(rowKey(row({ ticker: "SPY", market: "US" })), /^@ticker:/);
});

test("a saved carteira names the rows it cannot hold", () => {
  const rows = liveToRows([
    { ticker: "IVVB11", market: "B3", target_pct: 50 },
    { ticker: "SPY", market: "US", target_pct: 25 },
    { ticker: "BTCUSDT", market: "CRYPTO", target_pct: 25 },
  ]);
  assert.deepEqual(offB3Rows(rows), ["SPY · EUA", "BTCUSDT · cripto"]);
  assert.deepEqual(offB3Rows(specToRows(THIRDS)), []);
});

test("the assets in play stay pickable after a row moves off them", () => {
  const saved = specTickers(THIRDS);
  assert.deepEqual(saved, [{ ticker: "DIVO11.SA", market: "B3" }]);
  const rows = specToRows(THIRDS);
  const moved: SleeveRow[] = [{ strategy_ref: "MomSharpe", target_pct: "34" }, ...rows.slice(1)];
  assert.deepEqual(heldAssets(moved, saved), [{ ticker: "DIVO11.SA", market: "B3" }]);
  // Each once, saved first, whatever the spelling.
  const withSearch = [...rows, { strategy_ref: "", ticker: "DIVO11", market: "B3" as const, target_pct: "1" }];
  assert.deepEqual(heldAssets(withSearch, saved), [{ ticker: "DIVO11.SA", market: "B3" }]);
  assert.deepEqual(heldAssets([], []), []);
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

// ── the picker's options ─────────────────────────────────────────────────────

const hit = (h: Partial<AssetHit> & Pick<AssetHit, "ticker" | "market">): AssetHit => ({
  name: null, asset_class: "STOCK", price_brl: 10, price_date: "2026-11-12", ...h,
});

const HITS: AssetHit[] = [
  hit({ ticker: "SPY", market: "US", name: "SPDR S&P 500 ETF Trust", asset_class: "ETF", price_brl: 3294 }),
  hit({ ticker: "PETR4", market: "B3", name: "PETROBRAS", price_brl: 40 }),
  hit({ ticker: "BTCUSDT", market: "CRYPTO", asset_class: "CRYPTO", price_brl: 540000 }),
  hit({ ticker: "QQQ", market: "US", asset_class: "ETF", price_brl: null }),
];

const input = (over: Partial<PickerInput>): PickerInput => ({
  query: "",
  builtinRefs: ["MomSharpe", "Teva Value"],
  userRefs: ["Meu momentum"],
  needsFunds: new Set(["Teva Value"]),
  held: [],
  hits: [],
  carteira: false,
  ...over,
});

test("an empty query lists cash, then the strategies, grouped", () => {
  const opts = pickerOptions(input({}));
  assert.deepEqual(opts.map((o) => [o.group, o.label]), [
    ["Caixa", "Caixa (CDI)"],
    ["Estratégias da plataforma", "MomSharpe"],
    ["Estratégias da plataforma", "Teva Value"],
    ["Minhas estratégias de regra", "Meu momentum"],
  ]);
  assert.equal(opts[2].detail, "precisa de fundamentos");
  assert.deepEqual(opts[0].pick, { strategy_ref: CASH_REF, ticker: undefined, market: undefined });
});

test("a query filters strategies locally, accents and case aside", () => {
  assert.deepEqual(pickerOptions(input({ query: "mom" })).map((o) => o.label), ["MomSharpe", "Meu momentum"]);
  assert.deepEqual(pickerOptions(input({ query: "CAIXA" })).map((o) => o.label), ["Caixa (CDI)"]);
  assert.deepEqual(pickerOptions(input({ query: "fundamentos" })).map((o) => o.label), ["Teva Value"]);
  assert.deepEqual(pickerOptions(input({ query: "nada disso" })), []);
});

// The asset half of the list.
const assets = (opts: ReturnType<typeof pickerOptions>) =>
  opts.filter((o) => o.group.startsWith("Ativos ·"));

test("search hits come grouped by market, priced, and choose a ticker row", () => {
  const opts = assets(pickerOptions(input({ query: "", hits: HITS })));
  assert.deepEqual(opts.map((o) => [o.group, o.label]), [
    ["Ativos · B3", "PETR4"],
    ["Ativos · EUA", "SPY · EUA"],
    ["Ativos · EUA", "QQQ · EUA"],
    ["Ativos · cripto", "BTCUSDT · cripto"],
  ]);
  const spy = opts[1];
  assert.deepEqual(spy.pick, { strategy_ref: "", ticker: "SPY", market: "US" });
  assert.match(spy.detail ?? "", /SPDR S&P 500 ETF Trust · ETF · R\$\s?3\.294/);
  assert.equal(spy.disabledReason, undefined);
  // Unpriced: shown, never choosable.
  assert.equal(opts[2].disabledReason, "sem cotação no plano de preços");
});

test("editing a saved carteira shows US and crypto assets disabled, saying why", () => {
  const opts = assets(pickerOptions(input({ query: "", hits: HITS, carteira: true })));
  const why = Object.fromEntries(opts.map((o) => [o.label, o.disabledReason]));
  assert.equal(why["PETR4"], undefined);
  assert.match(why["SPY · EUA"] ?? "", /só nos sleeves ativos/);
  assert.match(why["BTCUSDT · cripto"] ?? "", /só nos sleeves ativos/);
});

test("an asset already in play is listed once, under the held group", () => {
  const held = [{ ticker: "PETR4", market: "B3" as const }];
  const opts = pickerOptions(input({ query: "petr", hits: HITS.slice(1, 2), held }));
  assert.deepEqual(opts.map((o) => [o.group, o.label]), [["Ativos nesta alocação", "PETR4"]]);
  assert.equal(new Set(pickerOptions(input({ hits: HITS, held })).map((o) => o.id)).size,
    pickerOptions(input({ hits: HITS, held })).length);
});

test("hits still on screen for an earlier query are not offered for the new one", () => {
  // The reviewer's case: "petr" was searched, then "4" typed; until the search
  // for "petr4" lands, the list still holds the hits for "petr".
  const stale = [hit({ ticker: "PETR3", market: "B3", name: "PETROBRAS" }),
                 hit({ ticker: "PETR4", market: "B3", name: "PETROBRAS" })];
  const before = pickerOptions(input({ query: "petr", hits: stale }));
  const after = pickerOptions(input({ query: "petr4", hits: stale }));
  assert.deepEqual(assets(after).map((o) => o.label), ["PETR4"]);
  // Enter takes what the query names, never PETR3.
  const state = reconcile({ open: true, active: -1 }, before, after);
  assert.equal(after[onKey(state, "Enter", after).select!].label, "PETR4");
  // A name match still counts — "petrobras" is PETR3's name, not its ticker.
  assert.deepEqual(assets(pickerOptions(input({ query: "petrobras", hits: stale })))
    .map((o) => o.label), ["PETR3", "PETR4"]);
});

test("while the search is unsettled, nothing is pre-selected for Enter", () => {
  const stale = [hit({ ticker: "PETR4", market: "B3" })];
  const before = pickerOptions(input({ query: "pe", hits: stale }));
  const after = pickerOptions(input({ query: "pet", hits: stale }));
  const waiting = reconcile({ open: true, active: -1 }, before, after, false);
  assert.equal(waiting.active, -1);
  assert.equal(onKey(waiting, "Enter", after).select, undefined);
  // An option the user had already moved to is kept, settled or not.
  const mom = pickerOptions(input({ query: "mom", hits: stale }));
  const moms = pickerOptions(input({ query: "momS", hits: stale }));
  const moved = reconcile({ open: true, active: 0 }, mom, moms, false);
  assert.equal(moms[moved.active].label, "MomSharpe");
});

test("rows get a stable identity once, and keep it through edits", () => {
  const rows = withUid(specToRows(THIRDS));
  assert.equal(new Set(rows.map((r) => r.uid)).size, 3);
  const edited = withUid([{ ...rows[1], target_pct: "50" }, ...rows.slice(2)]);
  assert.deepEqual(edited.map((r) => r.uid), [rows[1].uid, rows[2].uid]);
  // Never part of what is sent.
  assert.deepEqual(rowToSleeve(rows[0]), { ticker: "DIVO11.SA", target_pct: 34 });
});
