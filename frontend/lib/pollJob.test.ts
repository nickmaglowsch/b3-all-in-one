import { strict as assert } from "node:assert";
import test from "node:test";

import { pollJob } from "./pollJob.ts";

type J = { id: string; status: string; n?: number };

function clock() {
  let t = 0;
  return { now: () => t, sleep: async (ms: number) => void (t += ms) };
}

test("reads the job until it stops being busy, and returns the last read", async () => {
  const seen = ["pending", "running", "running", "done"];
  const reads: string[] = [];
  const job = await pollJob<J>(
    async () => ({ id: "j1", status: seen.shift()! }),
    async (id) => (reads.push(id), { id, status: seen.shift()!, n: reads.length }),
    { everyMs: 2000, giveUpMs: 60_000, timedOut: () => new Error("late"), ...clock() },
  );
  assert.deepEqual(job, { id: "j1", status: "done", n: 3 });
  assert.deepEqual(reads, ["j1", "j1", "j1"]);
});

test("a job that finishes on submission is never re-read", async () => {
  let reads = 0;
  const job = await pollJob<J>(
    async () => ({ id: "j", status: "error" }),
    async (id) => (reads++, { id, status: "done" }),
    { everyMs: 1, giveUpMs: 1, timedOut: () => new Error("late"), ...clock() },
  );
  assert.equal(job.status, "error");
  assert.equal(reads, 0);
});

test("gives up with the caller's error once the deadline passes", async () => {
  await assert.rejects(
    pollJob<J>(
      async () => ({ id: "j", status: "pending" }),
      async (id) => ({ id, status: "running" }),
      { everyMs: 1000, giveUpMs: 5000, timedOut: () => new Error("demorou"), ...clock() },
    ),
    /demorou/,
  );
});
