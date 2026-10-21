import { strict as assert } from "node:assert";
import test from "node:test";

import { withQuery } from "./url.ts";

// O "+" de um título do Tesouro chegava ao servidor como espaço; o nome não
// casava com nada e o ativo saía da seleção calado.
test("um ticker de renda fixa chega ao servidor como foi escrito", () => {
  const url = withQuery("/portfolio/index", { tickers: "TESOURO IPCA+ 2029,PETR4" });
  assert.equal(url, "/portfolio/index?tickers=TESOURO+IPCA%2B+2029%2CPETR4");
  // O que o servidor lê de volta (Starlette decodifica como URLSearchParams).
  const back = new URL(url, "https://api").searchParams.get("tickers");
  assert.equal(back, "TESOURO IPCA+ 2029,PETR4");
  assert.deepEqual(back!.split(","), ["TESOURO IPCA+ 2029", "PETR4"]);
});

test("parâmetros vazios saem, e sem nenhum não sobra '?'", () => {
  assert.equal(withQuery("/x", { a: null, b: undefined }), "/x");
  assert.equal(withQuery("/x", { a: 1, b: null, c: "d&e" }), "/x?a=1&c=d%26e");
});
