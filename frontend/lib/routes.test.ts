import { strict as assert } from "node:assert";
import test from "node:test";

import {
  APP_HOME,
  entryHref,
  isPublicRoute,
  postLoginTarget,
  PUBLIC_ROUTES,
} from "./routes.ts";

test("as rotas públicas são exatamente as quatro da lista", () => {
  assert.deepEqual(PUBLIC_ROUTES, ["/", "/login", "/auth/callback", "/privacidade"]);
  for (const r of PUBLIC_ROUTES) assert.equal(isPublicRoute(r), true, r);
});

// O callback é onde o token *chega*. Dentro do shell, as queries da barra lateral
// saíam antes de ele ser guardado, voltavam 401, e o 401 apagava o token recém-
// -entregue pelo Google: o login que "às vezes" só passava limpando cookies.
test("o callback do OAuth renderiza sem o shell, e só ele", () => {
  assert.equal(isPublicRoute("/auth/callback"), true);
  assert.equal(isPublicRoute("/auth"), false);
  assert.equal(isPublicRoute("/auth/callback/outra-coisa"), false);
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

// ── porta de entrada ──────────────────────────────────────────────────────────

test("o destino do app é uma rota guardada, não uma pública", () => {
  assert.equal(APP_HOME, "/portfolio");
  assert.equal(isPublicRoute(APP_HOME), false, "APP_HOME atrás da guarda");
});

// A queixa que originou isto: quem já entrou clica "Entrar" na landing e volta
// para a tela de login — a sessão existe, mas o site não tem porta para ela.
test("com sessão, a landing entra no app; sem sessão, na tela de login", () => {
  assert.equal(entryHref(true), APP_HOME);
  assert.equal(entryHref(false), "/login");
});

test("o `next` carregado pela guarda é respeitado, com query e hash", () => {
  for (const next of ["/irpf", "/bench/run?serie=1", "/carteiras#nova"]) {
    assert.equal(postLoginTarget(next), next);
  }
});

test("sem `next` utilizável, o login cai no app", () => {
  for (const next of [null, undefined, ""]) {
    assert.equal(postLoginTarget(next), APP_HOME);
  }
});

// `next` vem da URL, então é entrada de quem mandar o link. Qualquer coisa que o
// navegador resolva para outra origem é redirecionamento aberto — e `/\` conta:
// os navegadores tratam a barra invertida como a segunda barra de `//`.
test("nenhum `next` de fora do site sobrevive", () => {
  for (const next of [
    "//evil.com",
    "/\\evil.com",
    "\\/evil.com",
    "https://evil.com",
    "http://evil.com/portfolio",
    "javascript:alert(1)",
    "portfolio",
  ]) {
    assert.equal(postLoginTarget(next), APP_HOME, next);
  }
});

// A guarda só manda para rota guardada — ela é a única coisa que escreve `next`.
// Um `next` público é o laço (`/login` de volta para `/login`) ou a própria porta
// que a pessoa estava tentando atravessar (`/`).
test("um `next` público não é destino de login", () => {
  for (const next of ["/", "/login", "/login?next=%2Firpf", "/privacidade", "/auth/callback"]) {
    assert.equal(postLoginTarget(next), APP_HOME, next);
  }
});

// O parser de URL apaga tab/LF/CR em qualquer posição: `/\t/evil.example` passa
// num teste da string crua e o navegador resolve `//evil.example`.
test("caractere de controle não abre caminho para outra origem", () => {
  for (const next of [
    "/\t/evil.example",
    "/\n/evil.example/x",
    "/\r/evil.example",
    "/\t\\evil.example",
    "/ /evil.example",
  ]) {
    assert.equal(postLoginTarget(next), APP_HOME, JSON.stringify(next));
  }
});

// O parser resolve segmentos de ponto: `/.//evil.example` vira o caminho
// `//evil.example`, que o router resolve para outra origem.
test("segmento de ponto não abre caminho para outra origem", () => {
  for (const next of [
    "/.//evil.example",
    "/..//evil.example",
    "/a/..//evil.example",
    "/%2e//evil.example",
    "/./\\evil.example",
    "/.\\/evil.example",
  ]) {
    assert.equal(postLoginTarget(next), APP_HOME, JSON.stringify(next));
  }
});
