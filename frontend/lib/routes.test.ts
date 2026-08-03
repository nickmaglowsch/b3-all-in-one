import { strict as assert } from "node:assert";
import test from "node:test";

import { isPublicRoute, PUBLIC_ROUTES } from "./routes.ts";

test("as rotas públicas são exatamente as três da lista", () => {
  assert.deepEqual(PUBLIC_ROUTES, ["/", "/login", "/privacidade"]);
  for (const r of PUBLIC_ROUTES) assert.equal(isPublicRoute(r), true, r);
});

// AppShell devolve a página crua para rota pública — sem chrome e sem guarda de
// login. Então "público" é decisão de autenticação, e uma regra de prefixo torna
// pública uma subárvore inteira, incluindo o que ainda não foi escrito nela.
test("nenhuma outra rota é pública, nem por prefixo", () => {
  for (const r of [
    "/portfolio",
    "/irpf",
    "/conta",
    "/carteiras",
    "/privacidade/interna",
    "/login/callback",
    "/variantes",
    "/variantes/tudo",
    "/variantes/qualquer-coisa",
  ]) {
    assert.equal(isPublicRoute(r), false, `${r} não devia dispensar a guarda`);
  }
});
