import { strict as assert } from "node:assert";
import test from "node:test";

import {
  REBALANCE_FREQS,
  compactBRL,
  formatDate,
  formatDateTime,
  formatInt,
  formatMetricPct,
  formatMoney,
  formatPercent,
  formatQty,
  formatRatio,
} from "./format.ts";

// Intl separa "R$" do número com espaço não-quebrável (U+00A0). Comparar contra
// um espaço comum falha por um byte invisível, o que não é o que se quer testar.
const plain = (s: string) => s.replace(/ /g, " ");

test("dinheiro sai em BRL pt-BR, com vírgula decimal e ponto de milhar", () => {
  assert.equal(plain(formatMoney(1234.5)), "R$ 1.234,50");
  assert.equal(plain(formatMoney(-1234.5)), "-R$ 1.234,50");
});

// O "+" é o que distingue um lucro de um prejuízo numa coluna de variação, onde
// o "-" do Intl já carrega o outro lado.
test("o sinal explícito só aparece no positivo, e nunca duplica o negativo", () => {
  assert.equal(plain(formatMoney(10, true)), "+R$ 10,00");
  assert.equal(plain(formatMoney(-10, true)), "-R$ 10,00");
  assert.equal(plain(formatMoney(0, true)), "R$ 0,00", "zero não é ganho");
  assert.equal(plain(formatMoney(10)), "R$ 10,00", "sem pedir, sem sinal");
});

// A entrada é fração, não percentual. Trocar as duas convenções multiplica por
// cem o que a tela mostra — e um rendimento de 1,23% vira 123%.
test("percentual recebe fração: 0,0123 é 1,23% e 1 é 100%", () => {
  assert.equal(formatPercent(0.0123), "1,23%");
  assert.equal(formatPercent(1), "100,0%");
  assert.equal(formatPercent(-0.05), "-5,0%");
});

// Fração de cota de FII existe de verdade; arredondar quantidade para inteiro
// esconde a sobra que o IR cobra na venda.
test("quantidade preserva casas fracionárias e inteiro arredonda", () => {
  assert.equal(formatQty(1234.56789123), "1.234,56789123");
  assert.equal(formatInt(1234.6), "1.235");
  assert.equal(formatInt(1234), "1.234");
});

test("data ISO vira dd/mm/aaaa e um timestamp perde só a hora", () => {
  assert.equal(formatDate("2026-03-02"), "02/03/2026");
  assert.equal(formatDate("2026-03-02T23:30:00Z"), "02/03/2026");
});

test("data ausente vira travessão, e o que não é ISO passa direto", () => {
  for (const empty of [null, undefined, ""]) assert.equal(formatDate(empty), "—");
  assert.equal(formatDate("ontem"), "ontem");
});

// O motivo de a função fatiar a string em vez de usar `new Date`: a Pages roda em
// UTC, o desenvolvedor roda em BRT, e "2026-03-02" viraria 01/03 num deles.
test("a data não muda com o fuso da máquina que renderiza", () => {
  const tz = process.env.TZ;
  try {
    for (const zone of ["Pacific/Kiritimati", "Pacific/Midway", "UTC"]) {
      process.env.TZ = zone;
      assert.equal(formatDate("2026-03-02"), "02/03/2026", zone);
    }
  } finally {
    process.env.TZ = tz;
  }
});

// 01:00Z é 22:00 do dia anterior em São Paulo. É exatamente o caso em que fatiar
// a string (o que formatDate faz) responderia o dia errado.
test("o horário é o de São Paulo, e pode recuar o dia", () => {
  assert.equal(formatDateTime("2026-03-02T01:00:00Z"), "01/03/2026 às 22:00");
  assert.equal(formatDate("2026-03-02T01:00:00Z"), "02/03/2026", "o contraste");
});

test("o horário também não depende do fuso da máquina", () => {
  const tz = process.env.TZ;
  try {
    for (const zone of ["Pacific/Kiritimati", "Pacific/Midway", "UTC"]) {
      process.env.TZ = zone;
      assert.equal(formatDateTime("2026-03-02T01:00:00Z"), "01/03/2026 às 22:00", zone);
    }
  } finally {
    process.env.TZ = tz;
  }
});

test("timestamp ausente vira travessão e um valor ilegível passa direto", () => {
  for (const empty of [null, undefined, ""]) assert.equal(formatDateTime(empty), "—");
  // Sem a guarda de NaN sairia "Invalid Date" no lugar de uma data.
  assert.equal(formatDateTime("nunca"), "nunca");
});

// Métricas do motor já vêm em pontos percentuais (8.9 = 8,9%), não em fração.
test("métrica percentual acrescenta % ao número, sem multiplicar por cem", () => {
  assert.equal(formatMetricPct(8.9), "8,9%");
  assert.equal(formatMetricPct(-8.15), "-8,15%");
  assert.equal(formatMetricPct(8.9, true), "+8,9%");
  assert.equal(formatMetricPct(-8.15, true), "-8,15%");
  assert.equal(formatMetricPct(0, true), "0,0%", "zero não é ganho");
  assert.equal(formatMetricPct(null), "—");
});

test("razão (Sharpe, Calmar) sai com duas casas, e ausente vira travessão", () => {
  assert.equal(formatRatio(1.234), "1,23");
  assert.equal(formatRatio(-0.5), "-0,50");
  assert.equal(formatRatio(null), "—");
});

test("BRL compacto para eixos: mi, mil ou inteiro, sinal antes do R$", () => {
  assert.equal(compactBRL(1_250_000), "R$ 1,3 mi");
  assert.equal(compactBRL(-45_600), "-R$ 46 mil");
  assert.equal(compactBRL(999.4), "R$ 999");
  assert.equal(compactBRL(0), "R$ 0");
});

test("frequências de rebalance: códigos do motor com rótulos em português", () => {
  assert.deepEqual(REBALANCE_FREQS, [
    { value: "ME", label: "Mensal" },
    { value: "QE", label: "Trimestral" },
    { value: "W-FRI", label: "Semanal" },
  ]);
});
