import { strict as assert } from "node:assert";
import test from "node:test";

import { BASE, EMPRESARIAL, PAGOS, somaModulos, TUDO } from "./modules.ts";

// A frase que a seção de preço faz é "o Empresarial é mais caro que ter tudo".
// Se alguém mexer num preço e isso deixar de valer, o modelo inteiro passa a
// punir quem liga tudo — e nada na tela avisa. Este é o teste.
test("o Empresarial custa mais que ligar todos os módulos", () => {
  assert.equal(TUDO, PAGOS.reduce((s, m) => s + m.price, 0));
  assert.ok(EMPRESARIAL.price > TUDO, `${EMPRESARIAL.price} deveria passar de ${TUDO}`);
});

test("a base é grátis e os módulos são todos pagos", () => {
  assert.equal(BASE.price, 0);
  assert.ok(PAGOS.every((m) => m.price > 0));
});

test("somaModulos ignora id desconhecido e não conta duas vezes", () => {
  assert.equal(somaModulos([]), 0);
  assert.equal(somaModulos(["nada"]), 0);
  assert.equal(somaModulos(["irpf", "irpf"]), 19);
  assert.equal(somaModulos(PAGOS.map((m) => m.id)), TUDO);
});
