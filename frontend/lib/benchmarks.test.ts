// Run: npm test (node --test, native TS — no runner dependency).
import { test } from "node:test";
import assert from "node:assert/strict";
import { alignLevels, benchmarkNote, withBenchmarks } from "./benchmarks.ts";

const grid = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"];

test("a benchmark is forward-filled onto the chart's own grid", () => {
  // The rulers keep their own calendars — CDI accrues on bank business days,
  // the plane's grid is the exchange's. A day the benchmark did not publish is
  // a flat day, not a hole.
  const out = alignLevels(grid, { "2024-01-02": 100, "2024-01-04": 104 });
  assert.deepEqual(out, [100, 100, 104, 104]);
});

test("a level published between two sessions still lands on the next one", () => {
  // IPCA is monthly: its date is almost never a session.
  const out = alignLevels(grid, { "2024-01-03T00:00:00": 1.02 });
  assert.deepEqual(out, [undefined, 1.02, 1.02, 1.02]);
});

test("before a benchmark's first day there is a gap, never a zero", () => {
  // A zero would draw the ruler crashing to the floor and then leaping; the
  // honest shape is a line that simply has not started.
  const out = alignLevels(grid, { "2024-01-04": 104 });
  assert.deepEqual(out, [undefined, undefined, 104, 104]);
});

test("no levels at all is a column of gaps, not a flat line at nothing", () => {
  assert.deepEqual(alignLevels(grid, {}), [undefined, undefined, undefined, undefined]);
  assert.deepEqual(alignLevels([], { "2024-01-02": 100 }), []);
});

// ── overlaying onto the runs ──────────────────────────────────────────────────

// Typed the way the chart's rows are — one date plus a column per line — so the
// benchmark columns this adds are addressable in the assertions below.
const rows: { date: string; [column: string]: string | number }[] = [
  { date: "2024-01-02", run: 100_000 },
  { date: "2024-01-03", run: 101_000 },
  { date: "2024-01-04", run: 99_000 },
];

test("a ruler is scaled to the money the chart starts from", () => {
  // In reais the only reading that means anything is "the same capital, in this
  // instead" — a benchmark plotted in index points beside a NAV would flatten
  // both lines against an axis neither of them is on.
  const out = withBenchmarks(rows, { ibov: { "2024-01-02": 130000, "2024-01-04": 143000 } },
    100_000);
  assert.equal(out[0].ibov, 100_000);
  assert.equal(out[1].ibov, 100_000);
  assert.equal(out[2].ibov, 110_000);
});

test("scaling is off the benchmark's own first value inside the window", () => {
  // Not off its first stored value ever: the panel asks for the union of the
  // selected runs' windows, and a ruler must start where the chart starts.
  const out = withBenchmarks(rows, { cdi: { "2024-01-03": 2, "2024-01-04": 2.02 } },
    100_000);
  assert.equal(out[0].cdi, undefined);
  assert.equal(out[1].cdi, 100_000);
  assert.equal(out[2].cdi, 101_000);
});

test("a benchmark with nothing in the window adds no column at all", () => {
  const out = withBenchmarks(rows, { sp500: {} }, 100_000);
  assert.ok(out.every((r) => !("sp500" in r)));
});

test("the run columns and the dates come through untouched", () => {
  const out = withBenchmarks(rows, { ibov: { "2024-01-02": 130000 } }, 100_000);
  assert.deepEqual(out.map((r) => r.date), rows.map((r) => r.date));
  assert.deepEqual(out.map((r) => r.run), rows.map((r) => r.run));
  // New rows: the picker toggles benchmarks on and off over the same runs.
  assert.ok(out.every((r, i) => r !== rows[i]));
  assert.ok(rows.every((r) => !("ibov" in r)));
});

test("no benchmarks selected is the run rows, unchanged", () => {
  assert.deepEqual(withBenchmarks(rows, {}, 100_000), rows);
});

test("an anchor of zero cannot scale anything, so nothing is drawn", () => {
  // Reachable only if every selected run's curve is missing; drawing the raw
  // index points there would put 130.000 on an axis of reais.
  const out = withBenchmarks(rows, { ibov: { "2024-01-02": 130000 } }, 0);
  assert.ok(out.every((r) => !("ibov" in r)));
});

test("a line drawn off its proxy says so under the picker", () => {
  const note = "S&P 500 em reais";
  assert.equal(benchmarkNote(note, false), note);
  assert.equal(benchmarkNote(note, true, { proxy: "SPY", from: null }), "sem série para este período");
  assert.equal(
    benchmarkNote(note, false, { proxy: "SPY", from: "2021-01-04" }),
    "S&P 500 em reais · a partir de 04/01/2021, lido do SPY no plano (a série própria parou)",
  );
  // No `from`: the series had nothing in the window — never "it stopped".
  assert.equal(
    benchmarkNote(note, false, { proxy: "SPY", from: null }),
    "S&P 500 em reais · lido do SPY no plano no período todo (a série própria está vazia)",
  );
});
