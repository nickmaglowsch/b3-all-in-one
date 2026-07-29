import { strict as assert } from "node:assert";
import test from "node:test";

import { BASE, DARF, GANHO, impostoMes, linePath } from "./apuracao.ts";

test("a cadeia da apuração fecha", () => {
  assert.equal(GANHO, 2281.2);
  assert.equal(BASE, 1641.2);
  assert.equal(DARF, 246.18);
});

test("a isenção olha o total vendido, não o ganho", () => {
  assert.equal(impostoMes(19000, 5000), 0);
  assert.equal(impostoMes(20000, 5000), 0);
  assert.equal(impostoMes(20001, 1000), 150);
});

test("prejuízo abate a base e nunca vira imposto negativo", () => {
  assert.equal(impostoMes(30000, 1000, 400), 90);
  assert.equal(impostoMes(30000, 1000, 5000), 0);
});

test("linePath cobre a caixa e respeita uma escala externa", () => {
  assert.equal(linePath([0, 1]), "M4.0 36.0 L96.0 4.0");
  // Com lo/hi mais largos que a série, a linha fica dentro da caixa.
  assert.equal(linePath([0, 1], -1, 2), "M4.0 25.3 L96.0 14.7");
});
