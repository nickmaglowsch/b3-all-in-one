// Run: npm test (node --test, native TS — no runner dependency).
import { test } from "node:test";
import assert from "node:assert/strict";
import { isBusy, isDone, isFinished, type JobStatus } from "./jobStatus.ts";

const ALL: JobStatus[] = ["pending", "processing", "running", "done", "error"];

test("every status is exactly one of busy or finished", () => {
  for (const s of ALL) assert.notEqual(isBusy(s), isFinished(s), s);
});

test("busy is the same set for imports and backtests", () => {
  // The bench's own predicate once left "processing" out.
  assert.deepEqual(ALL.filter(isBusy), ["pending", "processing", "running"]);
});

test("done is the only status with a result; error is finished without one", () => {
  assert.deepEqual(ALL.filter(isDone), ["done"]);
  assert.deepEqual(ALL.filter(isFinished), ["done", "error"]);
});

test("an unknown or missing status is neither in flight nor finished", () => {
  for (const s of ["cancelled", "", undefined]) {
    assert.equal(isBusy(s), false);
    assert.equal(isDone(s), false);
    assert.equal(isFinished(s), false);
  }
});
