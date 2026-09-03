// Run: npm test (node --test, native TS — no runner dependency).
import { test } from "node:test";
import assert from "node:assert/strict";

// The cutoff is a calendar date in the *viewer's* timezone, so the assertions
// below only mean anything with one pinned. CI runs in UTC, where the whole
// hazard this module exists for — an evening in Brazil already being tomorrow in
// UTC — cannot occur, and the test would pass against the naive implementation.
// Set before the module is imported: Node re-reads TZ per Date operation, but
// keeping it at the top of the file leaves nothing to order.
process.env.TZ = "America/Sao_Paulo";

const { isoDaysAgo, purgeLabel, purgeQuery } = await import("./retention.ts");

// Local noon, so the assertions are about the arithmetic and not about the
// runner's timezone: any offset on Earth keeps this on the same calendar day.
const noon = (iso: string) => new Date(`${iso}T12:00:00`);

test("the cutoff is counted on the viewer's calendar", () => {
  assert.equal(isoDaysAgo(30, noon("2026-09-03")), "2026-08-04");
  assert.equal(isoDaysAgo(7, noon("2026-09-03")), "2026-08-27");
});

test("late in the evening the cutoff is still today's day, not UTC's tomorrow", () => {
  // 21:00 in São Paulo is already the 4th in UTC; a naive toISOString() would
  // cut a day short of what the user asked for.
  assert.equal(isoDaysAgo(30, new Date("2026-09-03T21:00:00-03:00")), "2026-08-04");
});

test("the cutoff crosses month and year boundaries", () => {
  assert.equal(isoDaysAgo(7, noon("2026-01-03")), "2025-12-27");
  assert.equal(isoDaysAgo(90, noon("2026-03-01")), "2025-12-01");
});

test("age and scope are independent criteria", () => {
  assert.equal(
    purgeQuery({ olderThanDays: 30, onlyErrors: false }, noon("2026-09-03")),
    "before=2026-08-04",
  );
  assert.equal(
    purgeQuery({ olderThanDays: 30, onlyErrors: true }, noon("2026-09-03")),
    "before=2026-08-04&status=error",
  );
  assert.equal(
    purgeQuery({ olderThanDays: null, onlyErrors: true }, noon("2026-09-03")),
    "status=error",
  );
});

test("a sweep with no criteria is refused here rather than at the API", () => {
  assert.equal(purgeQuery({ olderThanDays: null, onlyErrors: false }), null);
});

test("the confirm says what it will delete", () => {
  assert.equal(
    purgeLabel({ olderThanDays: 30, onlyErrors: true }),
    "Excluir rodadas com erro com mais de 30 dias",
  );
  assert.equal(
    purgeLabel({ olderThanDays: null, onlyErrors: false }),
    "Excluir todas as rodadas concluídas",
  );
});
