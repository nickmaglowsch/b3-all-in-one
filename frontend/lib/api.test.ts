// O wrapper por onde passa toda requisição: quem é o usuário (Bearer), sobre qual
// livro ele age (X-Portfolio-Id) e o que fazer quando a resposta é um não. As
// saídas de emergência do 402/404 são o que impede uma aba de ficar morta —
// morta de um jeito que o próprio app não consegue desfazer.
import { strict as assert } from "node:assert";
import test from "node:test";

const UUID = "11111111-2222-3333-4444-555555555555";

function memStorage() {
  const m = new Map<string, string>();
  return {
    getItem: (k: string) => m.get(k) ?? null,
    setItem: (k: string, v: string) => void m.set(k, v),
    removeItem: (k: string) => void m.delete(k),
  };
}

const state = { reloads: 0, events: [] as Event[] };
let instance = 0;

/** Instala um `window` falso e importa o módulo do zero — o seed da URL roda no
 *  escopo do módulo, então só um import novo o exercita. */
async function load(search = "", pathname = "/carteira") {
  state.reloads = 0;
  state.events = [];
  const win = {
    localStorage: memStorage(),
    sessionStorage: memStorage(),
    location: {
      search,
      pathname,
      href: "",
      reload: () => {
        state.reloads += 1;
      },
    },
    dispatchEvent: (e: Event) => {
      state.events.push(e);
      return true;
    },
  };
  Object.defineProperty(globalThis, "window", { value: win, configurable: true });
  const mod = await import(`./api.ts?n=${(instance += 1)}`);
  return { mod, win };
}

type Reply = (url: string, init: RequestInit) => Response;

function respond(reply: Reply) {
  const calls: { url: string; headers: Headers; init: RequestInit }[] = [];
  Object.defineProperty(globalThis, "fetch", {
    value: async (input: RequestInfo | URL, init: RequestInit = {}) => {
      calls.push({ url: String(input), headers: new Headers(init.headers), init });
      return reply(String(input), init);
    },
    configurable: true,
    writable: true,
  });
  return calls;
}

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

const ok = () => json(200, { ok: true });

async function rejection(p: Promise<unknown>) {
  try {
    await p;
  } catch (e) {
    return e as Error & { status: number; detail?: unknown };
  }
  throw new Error("a chamada devia ter falhado");
}

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
// Sessão
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

test("já estando no login, o 401 não redireciona para o próprio login", async () => {
  const { mod, win } = await load("", "/login");
  respond(() => json(401, { detail: "expirado" }));
  await rejection(mod.api("/auth/me"));
  assert.equal(win.location.href, "", "um redirect aqui é um laço");
});

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
  respond(() => json(404, { detail: "Carteira não encontrada" }));
  mod.setPortfolioId(UUID);
  await rejection(mod.api("/posicoes"));
  assert.equal(mod.getPortfolioId(), null);
  assert.equal(state.reloads, 1);
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

test("o Google login manda o browser para a URL de consentimento", async () => {
  const { mod, win } = await load();
  const calls = respond(() => json(200, { authorization_url: "https://accounts.google/x" }));
  await mod.googleLogin();
  // Sem `credentials: include` o cookie oauth_state não volta e o callback 400a.
  assert.equal(calls[0].init.credentials, "include");
  assert.equal(win.location.href, "https://accounts.google/x");
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
