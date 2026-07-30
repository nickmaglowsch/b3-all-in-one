"use client";

import { useMutation } from "@tanstack/react-query";
import { useState, useSyncExternalStore } from "react";
import { api } from "@/lib/api";
import { useSubscription } from "@/lib/hooks";
import { formatDate } from "@/lib/format";
import {
  alternaModulo,
  AVULSOS,
  BASE,
  BUNDLE,
  cartInicial,
  EMPRESARIAL,
  mesmoConjunto,
  nadaAMudar,
  nomeDe,
  precoDoCarrinho,
  TUDO,
} from "@/lib/modules";
import type { Subscription } from "@/lib/types";
import { PageHeader } from "@/components/ui/PageHeader";
import { Card } from "@/components/ui/Card";

// Stripe hands the browser back with ?checkout=ok|cancelado, and the paywall sends
// ?ligar=<módulo>. Read them once on mount (no useSearchParams => no Suspense
// boundary needed for a static route).
//
// useSyncExternalStore rather than setState-in-an-effect: the value is not React
// state, it is a browser value React has to be told about, and the server
// snapshot is what keeps the static export's HTML matching the first client
// render. Never subscribes — the query string does not change under this page.
const neverChanges = () => () => {};

function useQueryParam(name: string) {
  return useSyncExternalStore(
    neverChanges,
    () => new URLSearchParams(window.location.search).get(name),
    () => null,
  );
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
  confirmando,
  outcome,
  error,
}: {
  confirmando: boolean;
  outcome: string | null;
  error: unknown;
}) {
  return (
    <>
      {confirmando && <Aviso>Recebido — confirmando com o Stripe…</Aviso>}
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
  if (sub?.renews_at) return <>Renova em {formatDate(sub.renews_at)}</>;
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

/** O conjunto cobrado contra o qual a mudança foi enviada: [] voltando do
 *  checkout, o carrinho atual depois de alterar a assinatura viva. Só o webhook
 *  escreve entitlement, então a tela espera o cobrado sair desse conjunto. */
const esperandoPor = (enviado: string[] | null, outcome: string | null) =>
  enviado ?? (outcome === "ok" ? [] : null);

const aindaEsperando = (esperando: string[] | null, billed?: string[]) =>
  !!esperando && mesmoConjunto(billed ?? [], esperando);

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
  const outcome = useQueryParam("checkout");
  const ligar = useQueryParam("ligar");

  const [enviadoSobre, setEnviadoSobre] = useState<string[] | null>(null);
  const esperando = esperandoPor(enviadoSobre, outcome);
  const sub = useSubscription(esperando);
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
    mutationFn: (body: { path: string; method?: "PUT"; modules?: string[] }) =>
      api<{ url?: string; changed?: boolean }>(body.path, {
        method: body.method ?? "POST",
        body: body.modules ? JSON.stringify({ modules: body.modules }) : undefined,
      }),
    onSuccess: (r) => {
      if (r.url) window.location.href = r.url;
      else if (r.changed) setEnviadoSobre(billed ?? []);
    },
  });



  return (
    <>
      <PageHeader title="Assinatura" eyebrow="Conta">
        <Portal
          sub={sub.data}
          pending={go.isPending}
          onClick={() => go.mutate({ path: "/billing/portal" })}
        />
      </PageHeader>

      <Avisos
        confirmando={aindaEsperando(esperando, billed)}
        outcome={outcome}
        error={go.error}
      />
      <Ligados sub={sub.data} />
      <Fatura cart={cart} toggle={(id) => setChosen(alternaModulo(cart, id))} />
      <Assinar
        cart={cart}
        igual={nadaAMudar(cart, sub.data)}
        alterar={!!sub.data?.changeable}
        pending={go.isPending}
        onClick={() => go.mutate(destino(cart, sub.data?.changeable))}
      />
    </>
  );
}
