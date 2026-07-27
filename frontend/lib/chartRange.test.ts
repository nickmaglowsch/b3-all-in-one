// Run: npm test (node --test, native TS — no runner dependency).
import { test } from "node:test";
import assert from "node:assert/strict";
import { rebaseRows, sliceByDate, summarize } from "./chartRange.ts";

const rows = [
  { date: "2023-01-02", portfolio: 100, ibov: 100 },
  { date: "2023-06-15", portfolio: 110, ibov: 105 },
  { date: "2023-12-28", portfolio: 121, ibov: 90 },
];

test("an open range is the whole series", () => {
  assert.deepEqual(sliceByDate(rows, "", ""), rows);
});

test("both bounds are inclusive", () => {
  const out = sliceByDate(rows, "2023-06-15", "2023-12-28");
  assert.deepEqual(out.map((r) => r.date), ["2023-06-15", "2023-12-28"]);
});

test("either bound may be left open", () => {
  assert.equal(sliceByDate(rows, "2023-06-16", "").length, 1);
  assert.equal(sliceByDate(rows, "", "2023-06-15").length, 2);
});

test("a bound between sessions falls on the nearest ones inside it", () => {
  // No session on 01-03 or 12-01; the window is still the two days it contains.
  const out = sliceByDate(rows, "2023-01-03", "2023-12-01");
  assert.deepEqual(out.map((r) => r.date), ["2023-06-15"]);
});

test("an inverted range is empty, not the whole series", () => {
  assert.deepEqual(sliceByDate(rows, "2023-12-28", "2023-01-02"), []);
});

test("rebasing restarts every series at 100 on the window's first day", () => {
  // The point of the date filter: +10% over the second half is +10%, not the
  // +21% the book carries since inception.
  const out = rebaseRows(rows.slice(1));
  assert.deepEqual(out[0], { date: "2023-06-15", portfolio: 100, ibov: 100 });
  assert.equal(out[1].portfolio, 110);
  assert.equal(out[1].ibov, Number((90 / 105 * 100).toFixed(4)));
});

test("rebasing leaves the date alone and survives an empty window", () => {
  assert.deepEqual(rebaseRows([]), []);
  assert.equal(rebaseRows(rows)[0].date, "2023-01-02");
});

test("a series that is absent from the first row is left untouched", () => {
  // cdi arrives late (or not at all) — dividing by a base it has not got would
  // produce Infinity and blank the chart.
  const late = [{ date: "d1", portfolio: 100 }, { date: "d2", portfolio: 110, cdi: 7 }];
  assert.equal(rebaseRows(late)[1].cdi, 7);
});

test("summarize mirrors the engine: total, CAGR withheld under a year, max DD", () => {
  const short = summarize([100, 110], 60);
  assert.equal(short.total_return_pct, 10);
  assert.equal(short.ann_return_pct, null); // 60 days is not a CAGR

  const long = summarize([100, 121], 504);
  assert.ok(Math.abs((long.ann_return_pct ?? 0) - 10) < 0.05);

  // Peak 110 → trough 90 is -18.18%, not -10% off the start.
  assert.equal(summarize([100, 110, 90], 3).max_dd_pct, -18.18);
  assert.deepEqual(summarize([], 0), {
    total_return_pct: null,
    ann_return_pct: null,
    max_dd_pct: null,
  });
});
