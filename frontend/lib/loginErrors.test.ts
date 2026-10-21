import { strict as assert } from "node:assert";
import test from "node:test";

import { loginErrorMessage } from "./loginErrors.ts";

// Os códigos que a API manda em /login?erro= (platform_app/oauth_login.py). Cada
// um era um JSON cru no host da API, e a única saída era o botão Voltar.
test("cada código que a API emite tem a sua frase", () => {
  const codes = ["estado", "cancelado", "google", "email", "conta"];
  const sentences = codes.map(loginErrorMessage);
  for (const [i, s] of sentences.entries()) assert.ok(s, codes[i]);
  assert.equal(new Set(sentences).size, codes.length, "uma frase por motivo");
});

test("sem ?erro, nada a dizer", () => {
  for (const code of [null, undefined, ""]) assert.equal(loginErrorMessage(code), null);
});

// A API só manda para cá quando o login falhou; um código novo ainda é uma falha,
// e um nome de propriedade herdada não é uma mensagem.
test("um código desconhecido ainda diz que o login falhou", () => {
  for (const code of ["novo-motivo", "toString", "__proto__"]) {
    assert.match(loginErrorMessage(code) ?? "", /Não foi possível entrar/);
  }
});
