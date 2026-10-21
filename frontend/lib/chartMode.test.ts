// Run: npm test (node --test, native TS — no runner dependency).
import { test } from "node:test";
import assert from "node:assert/strict";
import { columnReturn, toReturnRows, toTwrRows } from "./chartMode.ts";

const nav = [
  { date: "2023-01-02", a: 100_000, b: 100_000 },
  { date: "2023-06-15", a: 110_000, b: 90_000 },
  { date: "2023-12-28", a: 121_000, b: 99_000 },
];

test("every series restarts at 0% on the window's first row", () => {
  const out = toReturnRows(nav);
  assert.deepEqual(out[0], { date: "2023-01-02", a: 0, b: 0 });
  assert.equal(out[1].a, 0.1);
  assert.equal(out[1].b, -0.1);
  assert.equal(out[2].a, 0.21);
});

test("the window is the base, not the run — a slice reads its own return", () => {
  // The whole point of the toggle sitting under the date filter: +10% over the
  // second half is +10%, not the +21% the run carries since it started.
  const out = toReturnRows(nav.slice(1));
  assert.equal(out[0].a, 0);
  assert.equal(out[1].a, 0.1);
});

test("a series that starts later starts at its own 0%, not at a cliff", () => {
  // Runs on different windows land on one date axis with holes in front. Basing
  // a late series on a value it does not have would either divide by nothing or
  // draw it dropping 100% on its first day.
  const rows = [
    { date: "d1", a: 100 },
    { date: "d2", a: 110, late: 50 },
    { date: "d3", a: 120, late: 55 },
  ];
  const out = toReturnRows(rows);
  assert.equal(out[0].late, undefined);
  assert.equal(out[1].late, 0);
  assert.equal(out[2].late, 0.1);
});

test("the date column survives, and so does an empty window", () => {
  assert.deepEqual(toReturnRows([]), []);
  assert.equal(toReturnRows(nav)[0].date, "2023-01-02");
});

test("a series based at zero is dropped rather than divided by nothing", () => {
  // Unreachable for a NAV curve and cheap to answer honestly: Infinity would
  // blank the whole chart, and 0 would claim the line was flat.
  const rows = [{ date: "d1", z: 0 }, { date: "d2", z: 5 }];
  const out = toReturnRows(rows);
  assert.equal(out[0].z, undefined);
  assert.equal(out[1].z, undefined);
});

test("returns are rounded, so float noise never reaches an axis label", () => {
  const rows = [{ date: "d1", a: 3 }, { date: "d2", a: 10 }];
  assert.equal(toReturnRows(rows)[1].a, 2.333333);
});

test("the source rows are left alone — the toggle must be reversible", () => {
  const before = JSON.parse(JSON.stringify(nav));
  toReturnRows(nav);
  assert.deepEqual(nav, before);
});

test("columnReturn is the return of one line over the rows it appears in", () => {
  assert.equal(columnReturn(nav, "a"), 0.21);
  assert.equal(columnReturn(nav, "b"), -0.01);
  // A line that is not on the chart has no return to report — not 0%.
  assert.equal(columnReturn(nav, "missing"), null);
  assert.equal(columnReturn([], "a"), null);
});

// The Simulação bug: R$10k, then R$90k more in a flat market, read as +900%.
test("toTwrRows does not count aportes as return", () => {
  const rows = [
    { date: "d1", s: 10_000 },
    { date: "d2", s: 100_000 },
  ];
  assert.deepEqual(toReturnRows(rows).map((r) => r.s), [0, 9]); // the old reading
  const twr = toTwrRows(rows, { s: { d1: 10_000, d2: 90_000 } });
  assert.deepEqual(twr.map((r) => r.s), [0, 0]);
});

test("toTwrRows chains each period's return net of that period's deposit", () => {
  const rows = [
    { date: "d1", s: 100 },
    { date: "d2", s: 110 }, // +10%, no cash
    { date: "d3", s: 220 }, // 100 deposited: (220 − 100) / 110 = +9,09%
  ];
  const twr = toTwrRows(rows, { s: { d3: 100 } });
  assert.deepEqual(twr.map((r) => r.s), [0, 0.1, 0.2]);
  // A withdrawal is cash too: 50 out, 60 left → (60 + 50) / 110 = flat.
  const out = toTwrRows(
    [{ date: "d1", s: 110 }, { date: "d2", s: 60 }],
    { s: { d2: -50 } },
  );
  assert.deepEqual(out.map((r) => r.s), [0, 0]);
});

test("toTwrRows starts each series at its first positive value", () => {
  const rows = [
    { date: "d1", s: 0, t: 50 },
    { date: "d2", s: 100, t: 55 },
    { date: "d3", s: 105, t: 55 },
  ];
  const twr = toTwrRows(rows, { s: { d2: 100 } });
  assert.equal("s" in twr[0], false, "no return on nothing");
  assert.deepEqual(twr.map((r) => r.t), [0, 0.1, 0.1]);
  assert.deepEqual(twr.slice(1).map((r) => r.s), [0, 0.05]);
});

test("columnReturn ignores the gap in front of a late series", () => {
  const rows = [
    { date: "d1", a: 100 },
    { date: "d2", a: 110, late: 50 },
    { date: "d3", a: 120, late: 55 },
  ];
  assert.equal(columnReturn(rows, "late"), 0.1);
});
