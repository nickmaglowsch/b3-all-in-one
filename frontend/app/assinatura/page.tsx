"use client";

import { useMutation } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { useBrowserValue, useSubscription } from "@/lib/hooks";
import { formatInstantDate } from "@/lib/format";
import {
  confirmView,
  ESPERA_MS,
  esperando,
  IDLE,
  vence,
  type Confirm,
} from "@/lib/checkoutConfirm";
import {
  alternaModulo,
  AVULSOS,
  BASE,
  BUNDLE,
  cartInicial,
  EMPRESARIAL,
  nadaAMudar,
  nomeDe,
  precoDoCarrinho,
  TUDO,
} from "@/lib/modules";
import type { Subscription } from "@/lib/types";
import { PageHeader } from "@/components/ui/PageHeader";
import { Card } from "@/components/ui/Card";

// The paywall sends ?ligar=<módulo>. Read on mount (no useSearchParams => no
// Suspense boundary needed for a static route).
//
// A browser value, not React state; nothing on this page changes `ligar`.
function useQueryParam(name: string) {
  return useBrowserValue(() => new URLSearchParams(window.location.search).get(name), null);
}

type Retorno = { outcome: string | null; at: number };

const lerRetorno = (lido: { current: Retorno | null }) =>
  (lido.current ??= {
    outcome: new URLSearchParams(window.location.search).get("checkout"),
    at: Date.now(),
  });

/** Stripe hands the browser back with ?checkout=ok|cancelado. Read once, then
 *  dropped from the address bar: left there, a reload or a bookmark replays
 *  "confirmando com o Stripe" over a subscription that settled long ago. The ref
 *  holds that first read — with the param gone, reading the URL again on the next
 *  render would answer null. `at` is when the page saw it: the start of the wait
 *  for a checkout that came back "ok". */
function useCheckoutReturn() {
  const lido = useRef<Retorno | null>(null);
  const retorno = useBrowserValue(() => lerRetorno(lido), null);
  useEffect(() => {
    // Read here too, so the drop below can never run before the first read.
    lerRetorno(lido);
    const url = new URL(window.location.href);
    if (!url.searchParams.has("checkout")) return;
    url.searchParams.delete("checkout");
    window.history.replaceState(null, "", url);
  }, []);
  return retorno;
}

/** Waiting turns into timedOut on the clock, not on a render: this ticks once,
 *  when the wait that started at `since` runs out. */
function usePrazo(c: Confirm): Confirm {
  const [agora, setAgora] = useState(0);
  const since = c.kind === "waiting" ? c.since : null;
  useEffect(() => {
    if (since === null) return;
    const t = setTimeout(() => setAgora(Date.now()), since + ESPERA_MS - Date.now());
    return () => clearTimeout(t);
  }, [since]);
  return vence(c, agora);
}

function Aviso({ children, tone }: { children: string; tone?: "erro" }) {
  return (
    <p
      role={tone ? "alert" : undefined}
      className={`mb-4 rounded-md border bg-panel px-4 py-3 text-sm ${
        tone ? "border-loss/40 text-loss" : "border-hairline text-muted"
      }`}
    >
      {children}
    </p>
  );
}

function Avisos({
  confirmacao,
  outcome,
  error,
}: {
  confirmacao: Confirm["kind"];
  outcome: string | null;
  error: unknown;
}) {
  return (
    <>
      {confirmacao === "waiting" && <Aviso>Recebido — confirmando com o Stripe…</Aviso>}
      {confirmacao === "timedOut" && (
        <Aviso>
          O Stripe ainda não confirmou a mudança. Costuma levar segundos; às vezes demora
          alguns minutos. Recarregue a página daqui a pouco para ver o que você assina.
        </Aviso>
      )}
      {outcome === "cancelado" && <Aviso>Checkout cancelado. Nada foi cobrado.</Aviso>}
      {error && (
        <Aviso tone="erro">
          {error instanceof Error ? error.message : "Erro inesperado."}
        </Aviso>
      )}
    </>
  );
}

/** Status do Stripe que guardam os itens (e a data) sem entitular. `past_due`
 *  não está aqui: dunning mantém o acesso, e tem aviso próprio. */
const SUSPENSA = ["incomplete", "incomplete_expired", "unpaid", "paused"];

function Renovacao({ sub }: { sub?: Subscription }) {
  if (sub?.status === "past_due") {
    return <>Pagamento pendente — atualize o cartão para não perder o acesso.</>;
  }
  // Cobrada e sem acesso: o Stripe manda a data de renovação junto, e a data
  // sozinha leria como "está tudo em ordem". `canceled` fica de fora — aquele
  // volta pelo checkout, e o texto abaixo já diz isso.
  if (sub?.status && SUSPENSA.includes(sub.status)) {
    return <>Assinatura suspensa — o acesso volta com o próximo pagamento.</>;
  }
  if (sub?.renews_at) return <>Renova em {formatInstantDate(sub.renews_at)}</>;
  return <>Sem assinatura ativa.</>;
}

function Ligados({ sub }: { sub?: Subscription }) {
  return (
    <Card title="O que você assina" className="mb-6">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p className="text-sm text-ink">
          {sub?.billed_modules.length
            ? sub.billed_modules.map(nomeDe).join(" · ")
            : "Só a Carteira, que é de graça."}
        </p>
        <p className="text-sm text-muted">
          <Renovacao sub={sub} />
        </p>
      </div>
    </Card>
  );
}

/** A fatura: uma linha por módulo, o preço é o custo de servidor dele, o total é a
 *  soma do que está marcado. Não existe escada, então não existe coluna. */
function Fatura({ cart, toggle }: { cart: string[]; toggle: (id: string) => void }) {
  const bundle = cart.includes(EMPRESARIAL);
  return (
    <div className="rounded-lg border border-hairline bg-panel">
      <div className="flex items-baseline justify-between gap-4 px-6 py-5">
        <div>
          <p className="text-sm font-semibold text-ink">{BASE.name}</p>
          <p className="mt-1 text-[13px] text-muted">{BASE.carga}</p>
        </div>
        <span className="shrink-0 font-mono text-[10px] font-medium uppercase tracking-[0.12em] text-muted">
          Sempre incluída
        </span>
      </div>

      {AVULSOS.map((m) => (
        <label
          key={m.id}
          className="flex cursor-pointer items-baseline justify-between gap-4 border-t border-hairline px-6 py-5 transition-colors hover:bg-hairline/20"
        >
          <span className="flex items-baseline gap-3">
            <input
              type="checkbox"
              checked={cart.includes(m.id)}
              onChange={() => toggle(m.id)}
              className="size-4 shrink-0 accent-navy"
            />
            <span>
              <span className="block text-sm font-semibold text-ink">{m.name}</span>
              <span className="mt-1 block text-[13px] text-muted">{m.carga}</span>
            </span>
          </span>
          <span className="shrink-0 font-display text-base font-extrabold tracking-tight text-ink tnum">
            R$ {m.price}
          </span>
        </label>
      ))}

      {/* O bundle fica fora da lista de propósito: não soma com nada. */}
      <div className="flex items-baseline justify-between gap-4 border-t border-hairline px-6 py-5">
        <div>
          <p className="text-sm font-semibold text-ink">{BUNDLE.name}</p>
          <p className="mt-1 text-[13px] text-muted">{BUNDLE.carga}</p>
          <label className="mt-3 flex cursor-pointer items-center gap-3 text-[13px] text-ink">
            <input
              type="checkbox"
              checked={bundle}
              onChange={() => toggle(EMPRESARIAL)}
              className="size-4 shrink-0 accent-navy"
            />
            Assinar o Empresarial em vez dos módulos avulsos
          </label>
        </div>
        <span className="shrink-0 font-display text-base font-extrabold tracking-tight text-ink tnum">
          R$ {BUNDLE.price}
        </span>
      </div>

      <div className="flex flex-wrap items-baseline justify-between gap-4 border-t border-hairline bg-hairline/20 px-6 py-6">
        <div>
          <p className="font-mono text-[10px] font-medium uppercase tracking-[0.14em] text-muted">
            Total por mês
          </p>
          <p className="mt-1 text-[13px] text-muted">
            {bundle
              ? `Todos os módulos. Avulsos, os ${AVULSOS.length} somam R$ ${TUDO}.`
              : `${cart.length} módulo${cart.length === 1 ? "" : "s"} marcado${cart.length === 1 ? "" : "s"}.`}
          </p>
        </div>
        <p
          aria-live="polite"
          className="font-display text-4xl font-extrabold tracking-tight text-ink tnum"
        >
          R$ {precoDoCarrinho(cart)}
        </p>
      </div>
    </div>
  );
}

/** Uma assinatura viva se altera; ela não se compra de novo. mode="subscription"
 *  sempre cria outra, e aí são duas faturas pelo mesmo acesso. */
const destino = (cart: string[], changeable?: boolean) =>
  changeable
    ? { path: "/billing/modules", method: "PUT" as const, modules: cart }
    : { path: "/billing/checkout", modules: cart };

/** Voltando do checkout com "ok", a espera é pelo cobrado sair de [], e começa
 *  quando a página leu o retorno. */
const doRetorno = (r: Retorno | null): Confirm =>
  r?.outcome === "ok" ? esperando([], r.at) : IDLE;

/** `from` é o cobrado no momento do clique, levado nas variáveis da mutação: lido
 *  no onSuccess, seria o do render em que a resposta chegou. */
type Envio = { path: string; method?: "PUT"; modules?: string[]; from: string[] };

function Portal({ sub, onClick, pending }: { sub?: Subscription; onClick: () => void; pending: boolean }) {
  if (!sub?.manageable) return null;
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={pending}
      className="rounded-md border border-hairline px-3 py-2 text-sm font-semibold text-ink transition-colors hover:bg-hairline/40 disabled:opacity-60"
    >
      Gerenciar no Stripe
    </button>
  );
}

/* Desligar módulo e cancelar são o portal do Stripe, que já resolve proração e o
   cancelar-no-fim-do-ciclo. */
function Assinar({
  cart,
  igual,
  alterar,
  pending,
  onClick,
}: {
  cart: string[];
  igual: boolean;
  alterar: boolean;
  pending: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={pending || cart.length === 0 || igual}
      className="mt-6 w-full rounded-md bg-navy px-4 py-3 text-sm font-semibold text-white transition-colors hover:bg-navy/90 disabled:opacity-60 md:w-auto md:px-8"
    >
      {igual && cart.length > 0
        ? "É o que você já assina"
        : `${alterar ? "Alterar" : "Assinar"} por R$ ${precoDoCarrinho(cart)}/mês`}
    </button>
  );
}

export default function AssinaturaPage() {
  const retorno = useCheckoutReturn();
  const ligar = useQueryParam("ligar");

  // Uma mudança enviada daqui manda; sem ela, o que o retorno do checkout diz.
  const [enviado, setEnviado] = useState<Confirm | null>(null);
  const confirm = usePrazo(enviado ?? doRetorno(retorno));
  const sub = useSubscription(confirm);
  const billed = sub.data?.billed_modules;

  // O carrinho parte do que já é cobrado — assinar de novo é "adicionar um
  // módulo", nunca "recomeçar do zero e perder o que eu tinha". Enquanto o
  // usuário não mexer, é a resposta da API que manda (daí `null`, e não um
  // setState dentro de um efeito, que renderiza duas vezes por definição).
  const [chosen, setChosen] = useState<string[] | null>(null);
  const cart = chosen ?? cartInicial(billed, ligar);

  // Checkout and portal answer with a Stripe-hosted URL to redirect to; changing
  // the modules of a live subscription happens server-side and answers
  // {changed}, and then we wait for the webhook like after a checkout.
  const go = useMutation({
    mutationFn: (body: Envio) =>
      api<{ url?: string; changed?: boolean }>(body.path, {
        method: body.method ?? "POST",
        body: body.modules ? JSON.stringify({ modules: body.modules }) : undefined,
      }),
    onSuccess: (r, body) => {
      if (r.url) window.location.href = r.url;
      else if (r.changed) setEnviado(esperando(body.from, Date.now()));
    },
  });

  // A redirect to Stripe is under way from the moment the URL arrives until the
  // page unloads; the buttons stay off for all of it, not just while the POST was
  // in flight. Coming Back from Stripe can restore this page from the bfcache
  // with that state intact, so a restored page forgets the redirect.
  const saindo = !!go.data?.url;
  const ocupado = go.isPending || saindo;
  const { reset } = go;
  useEffect(() => {
    const voltou = (e: PageTransitionEvent) => {
      if (e.persisted) reset();
    };
    window.addEventListener("pageshow", voltou);
    return () => window.removeEventListener("pageshow", voltou);
  }, [reset]);

  return (
    <>
      <PageHeader title="Assinatura" eyebrow="Conta">
        <Portal
          sub={sub.data}
          pending={ocupado}
          onClick={() => go.mutate({ path: "/billing/portal", from: billed ?? [] })}
        />
      </PageHeader>

      <Avisos
        confirmacao={confirmView(confirm, billed)}
        outcome={retorno?.outcome ?? null}
        error={go.error}
      />
      <Ligados sub={sub.data} />
      <Fatura cart={cart} toggle={(id) => setChosen(alternaModulo(cart, id))} />
      <Assinar
        cart={cart}
        igual={nadaAMudar(cart, sub.data)}
        alterar={!!sub.data?.changeable}
        pending={ocupado}
        onClick={() =>
          go.mutate({ ...destino(cart, sub.data?.changeable), from: billed ?? [] })
        }
      />
    </>
  );
}
