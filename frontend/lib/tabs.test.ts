import { strict as assert } from "node:assert";
import test from "node:test";

import { pickTab, stepTab } from "./tabs.ts";

const ids = ["geral", "fiscal", "posicoes"];

test("a aba pedida na URL vale quando existe", () => {
  assert.equal(pickTab(ids, "posicoes"), "posicoes");
});

test("aba ausente, vazia ou desconhecida cai na primeira", () => {
  assert.equal(pickTab(ids, null), "geral");
  assert.equal(pickTab(ids, ""), "geral");
  assert.equal(pickTab(ids, "rentabilidade"), "geral");
});

test("as setas andam e dão a volta nas pontas", () => {
  assert.equal(stepTab(ids, "geral", 1), "fiscal");
  assert.equal(stepTab(ids, "posicoes", 1), "geral");
  assert.equal(stepTab(ids, "geral", -1), "posicoes");
});
