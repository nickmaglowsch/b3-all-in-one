// A sessão vista pelo wrapper: quando um 401 encerra o login, como o login começa
// e como o token se renova. Separado de api.test.ts, que cuida de cabeçalhos,
// livros e respostas; o navegador falso dos dois é lib/apiHarness.ts.
import { strict as assert } from "node:assert";
import test from "node:test";

import { UUID, json, load, ok, rejection, respond } from "./apiHarness.ts";

// --------------------------------------------------------------------------
// 401
// --------------------------------------------------------------------------

test("um 401 limpa o token e devolve o usuário ao login", async () => {
  const { mod, win } = await load();
  respond(() => json(401, { detail: "expirado" }));
  mod.setToken("jwt-velho");
  const err = await rejection(mod.api("/dados"));
  assert.equal(err.status, 401);
  assert.equal(mod.getToken(), null);
  assert.equal(win.location.href, "/login");
});

// A corrida do /auth/callback: /me e /portfolios saíam sem token antes de a página
// guardar o do Google, e o 401 deles apagava o token novo.
test("um 401 de requisição sem token não apaga o token guardado depois", async () => {
  const { mod, win } = await load("", "/auth/callback");
  respond(() => {
    mod.setToken("jwt-recem-chegado"); // o callback guarda enquanto a resposta viaja
    return json(401, { detail: "Unauthorized" });
  });
  const err = await rejection(mod.api("/me"));
  assert.equal(err.status, 401);
  assert.equal(mod.getToken(), "jwt-recem-chegado");
  assert.equal(win.location.href, "", "a navegação para o app não pode ser cancelada");
});

// Outra aba, aberta antes deste login, com uma requisição no ar levando o token
// velho: o 401 dela fala do token velho, não do que acabou de ser emitido.
test("um 401 de um token anterior não derruba a sessão nova", async () => {
  const { mod, win } = await load();
  mod.setToken("jwt-velho");
  respond(() => {
    mod.setToken("jwt-novo");
    return json(401, { detail: "expirado" });
  });
  await rejection(mod.api("/dados"));
  assert.equal(mod.getToken(), "jwt-novo");
  assert.equal(win.location.href, "");
});

test("já estando no login, o 401 não redireciona para o próprio login", async () => {
  const { mod, win } = await load("", "/login");
  respond(() => json(401, { detail: "expirado" }));
  await rejection(mod.api("/auth/me"));
  assert.equal(win.location.href, "", "um redirect aqui é um laço");
});

// --------------------------------------------------------------------------
// Entrada
// --------------------------------------------------------------------------

// Navegação, não fetch: o cookie posto na resposta de um fetch cross-site é de
// terceiros, e Safari, iOS, Firefox, Brave e janela anônima o recusam.
test("o Google login é uma navegação para a API, sem fetch nenhum", async () => {
  const { mod, win } = await load();
  const calls = respond(ok);
  mod.googleLogin();
  assert.equal(calls.length, 0);
  assert.equal(win.location.href, `${mod.API_URL}/auth/google/login`);
});

// --------------------------------------------------------------------------
// Renovação da sessão
// --------------------------------------------------------------------------
// O token tem um prazo, e antes de /auth/refresh esse prazo era a sessão
// inteira: a aba parava de funcionar sozinha e o clique seguinte caía no
// /login. Todo mundo leu isso como "o deploy me deslogou".

const renewed = (t: string) => json(200, { access_token: t, token_type: "bearer" });

test("a renovação troca o token sem largar o livro selecionado", async () => {
  const { mod } = await load();
  const calls = respond(() => renewed("jwt-novo"));
  mod.setToken("jwt-velho");
  mod.setPortfolioId(UUID);
  await mod.refreshSession();
  assert.equal(calls[0].url, `${mod.API_URL}/auth/refresh`);
  assert.equal(calls[0].init.method, "POST");
  assert.equal(mod.getToken(), "jwt-novo");
  // Renovar não é entrar com outra conta: soltar o livro aqui tiraria o assessor
  // da carteira do cliente que ele está lendo, de hora em hora, sem motivo.
  assert.equal(mod.getPortfolioId(), UUID);
});

test("sem token não há o que renovar", async () => {
  const { mod } = await load();
  const calls = respond(() => renewed("jwt-novo"));
  await mod.refreshSession();
  assert.equal(calls.length, 0);
});

// Montagem, foco da janela e o intervalo caem juntos; sem o estrangulamento
// isso vira uma rajada de POSTs a cada troca de aba.
test("uma renovação recente não é repetida, mas `force` passa por cima", async () => {
  const { mod } = await load();
  const calls = respond(() => renewed("jwt-2"));
  mod.setToken("jwt-1");
  await mod.refreshSession();
  await mod.refreshSession();
  assert.equal(calls.length, 1);
  await mod.refreshSession(true);
  assert.equal(calls.length, 2);
});

// A API reiniciando no meio de um deploy é exatamente quando isto roda. Encerrar
// a sessão por causa disso seria o bug, não a correção.
test("uma falha que não é 401 deixa a sessão de pé", async () => {
  const { mod, win } = await load();
  respond(() => new Response("<html>502</html>", { status: 502, statusText: "Bad Gateway" }));
  mod.setToken("jwt-valido");
  await mod.refreshSession();
  assert.equal(mod.getToken(), "jwt-valido");
  assert.equal(win.location.href, "");
});

// O cap de idade da sessão no backend responde 401: aí sim acabou.
test("um 401 na renovação encerra a sessão e volta ao login", async () => {
  const { mod, win } = await load();
  respond(() => json(401, { detail: "Sessão expirada — entre novamente." }));
  mod.setToken("jwt-de-um-mes-atras");
  await mod.refreshSession();
  assert.equal(mod.getToken(), null);
  assert.equal(win.location.href, "/login");
});

// A API reiniciando é justamente quando o foco volta para a aba. Sem marcar a
// tentativa, cada troca de janela vira mais um POST contra um serviço fora do ar.
test("uma tentativa que falhou também segura a próxima", async () => {
  const { mod } = await load();
  const calls = respond(() => new Response("", { status: 502 }));
  mod.setToken("jwt-valido");
  await mod.refreshSession();
  await mod.refreshSession();
  assert.equal(calls.length, 1);
});
