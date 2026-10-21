// O wrapper por onde passa toda requisição: quem é o usuário (Bearer), sobre qual
// livro ele age (X-Portfolio-Id) e o que fazer quando a resposta é um não. As
// saídas de emergência do 402/404 são o que impede uma aba de ficar morta —
// morta de um jeito que o próprio app não consegue desfazer.
import { strict as assert } from "node:assert";
import test from "node:test";

import { UUID, json, load, ok, rejection, respond, state } from "./apiHarness.ts";

// --------------------------------------------------------------------------
// Cabeçalhos
// --------------------------------------------------------------------------

test("o token guardado vira Bearer, e sem token nada é enviado", async () => {
  const { mod } = await load();
  const calls = respond(ok);
  await mod.api("/dados");
  assert.equal(calls[0].headers.get("Authorization"), null);
  mod.setToken("jwt-abc");
  await mod.api("/dados");
  assert.equal(calls[1].headers.get("Authorization"), "Bearer jwt-abc");
  assert.equal(calls[1].url, `${mod.API_URL}/dados`);
});

test("o livro selecionado viaja em X-Portfolio-Id", async () => {
  const { mod } = await load();
  const calls = respond(ok);
  mod.setPortfolioId(UUID);
  await mod.api("/posicoes");
  assert.equal(calls[0].headers.get("X-Portfolio-Id"), UUID);
});

// Sem a isenção, uma seleção morta derruba justamente a chamada que a
// descobriria — e o app fica sem caminho de volta.
test("as rotas da conta não carregam o livro selecionado", async () => {
  const { mod } = await load();
  const calls = respond(ok);
  mod.setPortfolioId(UUID);
  for (const path of ["/portfolios", "/billing/checkout", "/privacy/export"]) {
    await mod.api(path);
  }
  for (const c of calls) assert.equal(c.headers.get("X-Portfolio-Id"), null, c.url);
});

test("um corpo de texto é JSON, um FormData é deixado em paz", async () => {
  const { mod } = await load();
  const calls = respond(ok);
  await mod.api("/x", { method: "POST", body: JSON.stringify({ a: 1 }) });
  assert.equal(calls[0].headers.get("Content-Type"), "application/json");
  // Marcar multipart à mão apagaria o boundary que o browser gera, e o upload
  // chegaria ilegível do outro lado.
  await mod.api("/upload", { method: "POST", body: new FormData() });
  assert.equal(calls[1].headers.get("Content-Type"), null);
});

// --------------------------------------------------------------------------
// Sessão e livro selecionado
// --------------------------------------------------------------------------

test("um login novo não herda o livro do login anterior", async () => {
  const { mod } = await load();
  mod.setPortfolioId(UUID);
  mod.setToken("jwt-de-outra-conta");
  assert.equal(mod.getPortfolioId(), null);
});

test("um link com ?carteira semeia a sessão antes da primeira query", async () => {
  const { mod } = await load(`?${"carteira"}=${UUID}`);
  assert.equal(mod.getPortfolioId(), UUID);
});

// O valor vai em X-Portfolio-Id em toda requisição seguinte: aceitar lixo
// transformaria a aba inteira em 400.
test("um ?carteira que não é UUID é ignorado", async () => {
  const { mod } = await load("?carteira=../../etc/passwd");
  assert.equal(mod.getPortfolioId(), null);
});

test("quem assina a troca de livro é avisado, até cancelar", async () => {
  const { mod } = await load();
  const seen: string[] = [];
  const stop = mod.subscribePortfolio(() => seen.push(mod.getPortfolioId() ?? "—"));
  mod.setPortfolioId(UUID);
  mod.clearPortfolioId();
  stop();
  mod.setPortfolioId(UUID);
  assert.deepEqual(seen, [UUID, "—"]);
});

// --------------------------------------------------------------------------
// Saídas de emergência
// --------------------------------------------------------------------------

const EMPRESARIAL = {
  error: "module_required",
  required_module: "empresarial",
  modules: ["base"],
};

test("assinatura vencida solta o livro do cliente em vez de abrir diálogo", async () => {
  const { mod } = await load();
  respond(() => json(402, { detail: EMPRESARIAL }));
  mod.setPortfolioId(UUID);
  const err = await rejection(mod.api("/posicoes"));
  assert.equal(err.status, 402);
  assert.equal(mod.getPortfolioId(), null, "sem soltar o livro, todo request 402a");
  assert.equal(state.reloads, 1);
  assert.equal(state.events.length, 0, "o diálogo não resolveria isto");
});

test("um livro que não resolve mais é solto, e a aba se recupera", async () => {
  const { mod } = await load();
  // O texto exato de platform_app/tenancy._acting_tenant.
  respond(() => json(404, { detail: "carteira não encontrada" }));
  mod.setPortfolioId(UUID);
  await rejection(mod.api("/posicoes"));
  assert.equal(mod.getPortfolioId(), null);
  assert.equal(state.reloads, 1);
});

// Um 404 de recurso dentro de um livro válido — backtest apagado, estratégia de
// código sem spec — não diz nada sobre o livro. Soltá-lo jogava o assessor para
// fora da carteira do cliente no meio de um clique.
test("um 404 de recurso não solta o livro nem recarrega", async () => {
  const { mod } = await load();
  respond(() => json(404, { detail: "backtest not found" }));
  mod.setPortfolioId(UUID);
  const err = await rejection(mod.api("/backtests/abc"));
  assert.equal(err.status, 404);
  assert.equal(mod.getPortfolioId(), UUID);
  assert.equal(state.reloads, 0);
});

// O 404 de uma rota de conta não fala do livro — soltá-lo aqui seria apagar a
// seleção do usuário por causa de um endpoint que nem a consulta.
test("um 404 de rota de conta não mexe no livro nem recarrega", async () => {
  const { mod } = await load();
  respond(() => json(404, { detail: "não existe" }));
  mod.setPortfolioId(UUID);
  await rejection(mod.api("/billing/plano-fantasma"));
  assert.equal(mod.getPortfolioId(), UUID);
  assert.equal(state.reloads, 0);
});

test("um 402 de outro módulo abre o diálogo de upgrade, uma vez só", async () => {
  const { mod } = await load();
  const detail = { error: "module_required", required_module: "bench", modules: ["base"] };
  respond(() => json(402, { detail }));
  const err = await rejection(mod.api("/backtest"));
  assert.equal(state.events.length, 1);
  assert.equal(state.events[0].type, mod.PAYWALL_EVENT);
  assert.deepEqual((state.events[0] as CustomEvent).detail, detail);
  assert.equal(mod.isModuleRequired(err.detail), true);
  assert.equal(mod.requiredModuleOf(err), "bench");
});

// Um modal por cima de uma página que ninguém leu ainda é interrupção, não oferta.
test("uma query de carregamento não abre diálogo nenhum", async () => {
  const { mod } = await load();
  respond(() => json(402, { detail: { error: "module_required", required_module: "bench", modules: [] } }));
  await rejection(mod.api("/backtest", {}, { paywallDialog: false }));
  assert.equal(state.events.length, 0);
});

// --------------------------------------------------------------------------
// Respostas
// --------------------------------------------------------------------------

test("um 204 devolve vazio sem tentar ler um corpo que não existe", async () => {
  const { mod } = await load();
  respond(() => new Response(null, { status: 204 }));
  assert.equal(await mod.api("/carteiras/1"), undefined);
});

test("a mensagem de erro prefere a frase que o backend escreveu", async () => {
  const { mod } = await load();
  const cases: [unknown, string][] = [
    ["Extrato já importado.", "Extrato já importado."],
    [EMPRESARIAL, "Recurso de um módulo que você ainda não assina."],
    [{ message: "Cota de backtests esgotada." }, "Cota de backtests esgotada."],
  ];
  for (const [detail, expected] of cases) {
    respond(() => json(400, { detail }));
    assert.equal((await rejection(mod.api("/x"))).message, expected);
  }
});

// Um HTML de gateway não é JSON; sem a captura, o erro que chega à tela é o do
// parser em vez do status que explica o que houve.
test("um corpo que não é JSON cai no texto do status", async () => {
  const { mod } = await load();
  respond(() => new Response("<html>502</html>", { status: 502, statusText: "Bad Gateway" }));
  const err = await rejection(mod.api("/x"));
  assert.equal(err.message, "Bad Gateway");
  assert.equal(err.status, 502);
});

test("o dev-login guarda o token que devolve", async () => {
  const { mod } = await load();
  respond(() => json(200, { access_token: "jwt-dev", token_type: "bearer" }));
  assert.equal(await mod.devLogin(), "jwt-dev");
  assert.equal(mod.getToken(), "jwt-dev");
});

// A mensagem de um ApiError é a do servidor, escrita para o usuário; qualquer
// outra falha (rede, bug) não tem uma frase que valha mostrar, e cai no texto
// da tela.
test("errorText mostra a mensagem da API, e o texto da tela para o resto", async () => {
  const { mod } = await load();
  const fallback = "Falha ao salvar.";
  assert.equal(mod.errorText(new mod.ApiError(422, "Pesos somam 110%."), fallback), "Pesos somam 110%.");
  assert.equal(mod.errorText(new TypeError("Failed to fetch"), fallback), fallback);
  assert.equal(mod.errorText(null, fallback), fallback);
});
