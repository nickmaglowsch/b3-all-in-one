// Run: npm test (node --test, native TS — no runner dependency).
import assert from "node:assert/strict";
import { test } from "node:test";
import { DEFAULT_MARKET, marketBadge, matchesQuery } from "./strategyGallery.ts";
import type { Strategy } from "./types.ts";

function strategy(over: Partial<Strategy> = {}): Strategy {
  return {
    name: "LowVol",
    kind: "spec",
    description: "Baixa volatilidade, decil superior.",
    needs_fundamentals: false,
    params: [],
    ...over,
  };
}

test("the default market is not badged", () => {
  assert.equal(marketBadge(strategy({ market: DEFAULT_MARKET })), null);
});

test("a gallery entry with no market at all is not badged", () => {
  // An older cached /strategies response predates the field.
  assert.equal(marketBadge(strategy()), null);
});

test("the two non-default planes are badged, lowercased", () => {
  assert.equal(marketBadge(strategy({ name: "USLowVol", market: "US" })), "us");
  assert.equal(
    marketBadge(strategy({ name: "CryptoLowVol", market: "CRYPTO" })),
    "crypto",
  );
});

test("search matches the market, which appears nowhere in the prose", () => {
  const s = strategy({ name: "CryptoLowVol", market: "CRYPTO" });
  assert.ok(matchesQuery(s, "crypto"));
  assert.ok(matchesQuery(s, "CRYPTO"));
  assert.ok(matchesQuery(s, "  crypto "));
});

test("search still matches name and description", () => {
  const s = strategy({ market: DEFAULT_MARKET });
  assert.ok(matchesQuery(s, "lowvol"));
  assert.ok(matchesQuery(s, "volatilidade"));
});

test("a market search does not drag in the other plane", () => {
  const us = strategy({ name: "USLowVol", market: "US" });
  assert.equal(matchesQuery(us, "crypto"), false);
});

test("an empty query keeps everything", () => {
  assert.ok(matchesQuery(strategy(), ""));
  assert.ok(matchesQuery(strategy(), "   "));
});
