// Os classificadores de erro do api(): funções puras sobre (status, detail), sem
// window nem fetch — por isso aqui, fora do api.test.ts, que monta um navegador
// falso para cada caso.
import { strict as assert } from "node:assert";
import test from "node:test";

import { ApiError, isBookNotFound, requiredModuleOf } from "./api.ts";

// O único 404 que fala do livro selecionado, e não do recurso pedido dentro dele:
// o texto exato de platform_app/tenancy._acting_tenant.
test("isBookNotFound reconhece só o 404 do tenancy", () => {
  assert.equal(isBookNotFound(404, "carteira não encontrada"), true);
  assert.equal(isBookNotFound(404, "Carteira não encontrada"), true);
  assert.equal(isBookNotFound(404, "backtest not found"), false);
  assert.equal(isBookNotFound(404, "curves artifact missing"), false);
  assert.equal(isBookNotFound(404, null), false);
  assert.equal(isBookNotFound(404, { error: "x" }), false);
  assert.equal(isBookNotFound(400, "carteira não encontrada"), false);
});

test("requiredModuleOf só responde por um 402 de módulo", () => {
  const gate = { error: "module_required", required_module: "irpf", modules: [] };
  assert.equal(requiredModuleOf(new ApiError(402, "x", gate)), "irpf");
  assert.equal(requiredModuleOf(new ApiError(500, "x", gate)), null);
  assert.equal(requiredModuleOf(new ApiError(402, "x", "texto")), null);
  assert.equal(requiredModuleOf(new Error("rede")), null);
  assert.equal(requiredModuleOf(undefined), null);
});
