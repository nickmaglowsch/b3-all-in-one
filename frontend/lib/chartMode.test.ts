// Run: npm test (node --test, native TS — no runner dependency).
import { test } from "node:test";
import assert from "node:assert/strict";
import { columnReturn, toReturnRows } from "./chartMode.ts";

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

test("columnReturn ignores the gap in front of a late series", () => {
  const rows = [
    { date: "d1", a: 100 },
    { date: "d2", a: 110, late: 50 },
    { date: "d3", a: 120, late: 55 },
  ];
  assert.equal(columnReturn(rows, "late"), 0.1);
});
