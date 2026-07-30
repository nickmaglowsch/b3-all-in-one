import { strict as assert } from "node:assert";
import test from "node:test";

import { BASE, DARF, GANHO, impostoMes, linePath, serieLiquida } from "./apuracao.ts";

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

const cent = (values: number[]) => values.map((v) => Math.round(v * 100) / 100);

test("serieLiquida tributa cada ganho, não o resultado final", () => {
  assert.deepEqual(cent(serieLiquida([100, 110])), [100, 108.5]); // 15% dos 10 de ganho
  // Dois ganhos seguidos pagam duas vezes, e a base do segundo é o capital já
  // tributado — não é a mesma coisa que 15% do ganho acumulado no fim.
  assert.deepEqual(cent(serieLiquida([100, 110, 121])), [100, 108.5, 117.72]);
  assert.notEqual(117.72, 100 + 21 * 0.85);
});

test("um período de perda não paga imposto e abate o ganho seguinte", () => {
  assert.deepEqual(cent(serieLiquida([100, 90])), [100, 90]); // prejuízo não é tributado
  // A recuperação vem inteira: o prejuízo de 10 zera o ganho de 10.
  assert.deepEqual(cent(serieLiquida([100, 90, 100])), [100, 90, 100]);
  // Só o que passa do prejuízo acumulado é tributável.
  assert.deepEqual(cent(serieLiquida([100, 90, 100, 110])), [100, 90, 100, 108.5]);
});

test("linePath cobre a caixa e respeita uma escala externa", () => {
  assert.equal(linePath([0, 1]), "M4.0 36.0 L96.0 4.0");
  // Com lo/hi mais largos que a série, a linha fica dentro da caixa.
  assert.equal(linePath([0, 1], -1, 2), "M4.0 25.3 L96.0 14.7");
});
