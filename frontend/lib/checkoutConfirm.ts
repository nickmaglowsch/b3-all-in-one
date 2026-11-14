// Esperando o webhook do Stripe, à parte do React. Só o webhook escreve
// entitlement, então depois de um checkout (ou de alterar a assinatura viva) a
// tela espera o conjunto cobrado sair daquele contra o qual a mudança foi
// enviada — `from`: [] voltando do checkout, o cobrado de então depois de alterar.
//
// Três estados, e o terceiro é o que faltava: a espera tem prazo. Antes o aviso
// "confirmando com o Stripe" não tinha fim, enquanto o polling parava depois de
// 12 respostas *acumuladas* na entrada ["subscription"] do cache — compartilhada
// com /carteiras, de modo que uma segunda mudança na mesma sessão quase não
// consultava. O prazo agora é tempo de relógio contado desta espera.
//
// Puro, e só importa ./modules.ts: `npm test` roda isto com `node --test`.

import { mesmoConjunto } from "./modules.ts";

export type Confirm =
  | { kind: "idle" }
  | { kind: "waiting"; from: string[]; since: number }
  | { kind: "timedOut"; from: string[] };

export const IDLE: Confirm = { kind: "idle" };

/** Quanto esperar o webhook antes de dizer que não chegou. Em produção ele chega
 *  em poucos segundos; passar disso é falha de entrega, não lentidão. */
export const ESPERA_MS = 30_000;
export const POLL_MS = 2_000;

export const esperando = (from: string[], since: number): Confirm => ({
  kind: "waiting",
  from,
  since,
});

/** O cobrado ainda é o de antes? Sem resposta da API ainda não dá para dizer que
 *  mudou, então conta como "ainda não". */
const parado = (from: string[], billed?: string[]) =>
  !billed || mesmoConjunto(billed, from);

/** O que a tela mostra. Assim que o cobrado sai de `from` a espera acabou — vale
 *  também depois do prazo, quando um refetch tardio (foco na janela) traz o
 *  webhook que atrasou. */
export function confirmView(c: Confirm, billed?: string[]): Confirm["kind"] {
  if (c.kind === "idle" || !parado(c.from, billed)) return "idle";
  return c.kind;
}

/** Esperando, e o prazo venceu: vira timedOut. Qualquer outro estado fica. */
export function vence(c: Confirm, now: number): Confirm {
  return c.kind === "waiting" && now - c.since >= ESPERA_MS
    ? { kind: "timedOut", from: c.from }
    : c;
}

/** O refetchInterval de useSubscription: consulta enquanto espera, dentro do
 *  prazo e com o cobrado ainda igual a `from`. */
export function pollEvery(c: Confirm, billed: string[] | undefined, now: number): number | false {
  return c.kind === "waiting" && now - c.since < ESPERA_MS && parado(c.from, billed)
    ? POLL_MS
    : false;
}
