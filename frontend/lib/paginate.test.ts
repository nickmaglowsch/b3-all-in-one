import { strict as assert } from "node:assert";
import test from "node:test";

import { paginate } from "./paginate.ts";

const rows = Array.from({ length: 46 }, (_, i) => i + 1);

test("a primeira página traz os primeiros `size` itens", () => {
  const p = paginate(rows, 1, 15);
  assert.deepEqual(p.items, rows.slice(0, 15));
  assert.equal(p.pages, 4);
  assert.equal(p.from, 1);
  assert.equal(p.to, 15);
  assert.equal(p.total, 46);
});

test("a última página é parcial", () => {
  const p = paginate(rows, 4, 15);
  assert.deepEqual(p.items, [46]);
  assert.equal(p.from, 46);
  assert.equal(p.to, 46);
});

// Um filtro que reduz 46 linhas a 3 com o leitor na página 4 não pode mostrar
// uma tabela vazia — isso se lê como "nada encontrado".
test("página fora do intervalo cai na última que existe", () => {
  const p = paginate(rows.slice(0, 3), 4, 15);
  assert.equal(p.page, 1);
  assert.deepEqual(p.items, [1, 2, 3]);
});

test("página abaixo de 1 ou inválida vira a primeira", () => {
  assert.equal(paginate(rows, 0, 10).page, 1);
  assert.equal(paginate(rows, Number.NaN, 10).page, 1);
});

test("lista vazia: uma página, nada nela, de 0 a 0", () => {
  const p = paginate([], 3, 10);
  assert.equal(p.page, 1);
  assert.equal(p.pages, 1);
  assert.equal(p.from, 0);
  assert.equal(p.to, 0);
  assert.deepEqual(p.items, []);
});
