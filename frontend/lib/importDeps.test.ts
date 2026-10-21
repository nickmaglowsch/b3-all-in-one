import { strict as assert } from "node:assert";
import test from "node:test";

import {
  dependsOnImports,
  importsSettledSince,
  snapshotImports,
} from "./importDeps.ts";

test("o que vem dos extratos é invalidado; a conta e os catálogos não", () => {
  for (const key of [
    ["positions"],
    ["income"],
    ["trades", 50, 0, "date", "desc"],
    ["price-history"],
    ["portfolio-index", null],
    ["tax-summary"],
    ["tlh", "situation"],
    ["darf", 2026],
    ["target"],
    ["privacy-summary"],
  ]) {
    assert.equal(dependsOnImports(key), true, String(key[0]));
  }
  for (const key of [["me"], ["portfolios"], ["subscription"], ["strategies"], ["imports"]]) {
    assert.equal(dependsOnImports(key), false, String(key[0]));
  }
});

const row = (id: string, status: string) => ({ id, status });

test("um import que estava rodando e terminou conta como novo", () => {
  const prev = snapshotImports([row("a", "processing"), row("b", "done")]);
  assert.equal(importsSettledSince(prev, [row("a", "done"), row("b", "done")]), true);
  assert.equal(importsSettledSince(prev, [row("a", "error"), row("b", "done")]), true);
  assert.equal(importsSettledSince(prev, [row("a", "processing"), row("b", "done")]), false);
});

// Um arquivo pequeno pode ser processado antes do primeiro poll vê-lo rodando.
test("um import que aparece já terminado também conta", () => {
  const prev = snapshotImports([row("b", "done")]);
  assert.equal(importsSettledSince(prev, [row("c", "done"), row("b", "done")]), true);
  assert.equal(importsSettledSince(prev, [row("c", "pending"), row("b", "done")]), false);
});

test("a primeira carga da página não invalida nada", () => {
  assert.equal(importsSettledSince(null, [row("a", "done")]), false);
});
