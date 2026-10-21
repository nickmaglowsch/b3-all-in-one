import { strict as assert } from "node:assert";
import test from "node:test";

import { anoBasePadrao, anoCorrente, anosRecentes } from "./years.ts";

// A lista era 2025…2021 escrita à mão: em 2026 o ano dos DARFs vencendo não
// podia nem ser escolhido.
test("a lista começa no ano corrente e anda para trás", () => {
  assert.deepEqual(anosRecentes(2026), [2026, 2025, 2024, 2023, 2022, 2021]);
  assert.deepEqual(anosRecentes(2030, 3), [2030, 2029, 2028]);
});

test("o ano-base padrão é o que se declara agora: o anterior", () => {
  assert.equal(anoBasePadrao(2026), 2025);
});

// 31/12 às 22h em São Paulo já é 1º de janeiro em UTC.
test("o ano corrente é o de São Paulo, não o do UTC", () => {
  assert.equal(anoCorrente(new Date("2026-01-01T01:00:00Z")), 2025);
  assert.equal(anoCorrente(new Date("2026-01-01T03:00:00Z")), 2026);
  assert.equal(anoCorrente(new Date("2026-10-21T12:00:00Z")), 2026);
});
