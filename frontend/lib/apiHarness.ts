// Um navegador falso para os testes de lib/api.ts: `window` com storage em
// memória, `fetch` com resposta escolhida pelo teste, e um import novo do módulo a
// cada caso. Compartilhado por api.test.ts e session.test.ts — sem ele cada
// arquivo carregaria a própria cópia, e as duas acabariam divergindo.

export const UUID = "11111111-2222-3333-4444-555555555555";

function memStorage() {
  const m = new Map<string, string>();
  return {
    getItem: (k: string) => m.get(k) ?? null,
    setItem: (k: string, v: string) => void m.set(k, v),
    removeItem: (k: string) => void m.delete(k),
  };
}

export const state = { reloads: 0, events: [] as Event[] };
let instance = 0;

/** Instala um `window` falso e importa o módulo do zero — o seed da URL roda no
 *  escopo do módulo, então só um import novo o exercita. */
export async function load(search = "", pathname = "/carteira") {
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

export function respond(reply: Reply) {
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

export const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

export const ok = () => json(200, { ok: true });

export async function rejection(p: Promise<unknown>) {
  try {
    await p;
  } catch (e) {
    return e as Error & { status: number; detail?: unknown };
  }
  throw new Error("a chamada devia ter falhado");
}
