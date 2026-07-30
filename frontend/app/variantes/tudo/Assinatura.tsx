"use client";

// A assinatura como fatura, não como grade de cartões.
//
// Quatro cartões de tier obrigam o leitor a comparar colunas para descobrir onde
// ele cai. Uma lista com uma caixa por módulo e um total embaixo faz a pergunta
// certa uma vez — "o que eu uso?" — e o total responde. E é o único desenho em
// que a razão do preço cabe ao lado do preço: cada linha diz o que o servidor
// gasta, que é a regra inteira do modelo.
import Link from "next/link";
import { useState } from "react";

import { AVULSOS, BASE, BUNDLE, somaModulos, TUDO } from "@/lib/modules";

export function Assinatura() {
  // Abre com o IRPF ligado, não com nada: um total em R$ 0 na primeira pintura
  // lê como "de graça" e esconde que o número se move. Com um módulo ligado o
  // primeiro clique é a descoberta, e é desmarcar — não marcar.
  const [ligados, setLigados] = useState<string[]>(["irpf"]);
  const total = somaModulos(ligados);
  const alterna = (id: string) =>
    setLigados((atual) => (atual.includes(id) ? atual.filter((x) => x !== id) : [...atual, id]));

  return (
    <section id="planos" className="scroll-mt-20 border-t border-hairline py-16 md:py-20">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <h2 className="max-w-xl font-display text-3xl font-extrabold tracking-tight text-ink">
          Você paga pelo que o servidor roda.
        </h2>
        <p className="max-w-sm text-sm leading-relaxed text-muted">
          Sem escada de planos. Ligue os módulos que usa, desligue no mês que não usar.
        </p>
      </div>

      <div className="mt-8 overflow-hidden rounded-lg border border-hairline bg-panel">
        {/* A base não é uma opção, então não tem caixa: tem a palavra "sempre". */}
        <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1 border-b border-hairline bg-paper px-5 py-4">
          <div>
            <p className="font-mono text-[11px] font-medium uppercase tracking-[0.14em] text-navy">
              Sempre incluída
            </p>
            <p className="mt-1 text-sm font-semibold text-ink">{BASE.name}</p>
          </div>
          <p className="text-sm text-muted">{BASE.carga}</p>
          <p className="font-display text-xl font-extrabold text-ink tnum">R$ 0</p>
        </div>

        {AVULSOS.map((m) => {
          const on = ligados.includes(m.id);
          return (
            <label
              key={m.id}
              className={`flex cursor-pointer items-start gap-4 border-b border-hairline px-5 py-4 transition-colors ${
                on ? "bg-navy/[0.03]" : "hover:bg-hairline/25"
              }`}
            >
              <input
                type="checkbox"
                checked={on}
                onChange={() => alterna(m.id)}
                className="mt-0.5 h-4 w-4 shrink-0 accent-navy"
              />
              <span className="min-w-0 flex-1">
                <span className="block text-sm font-semibold text-ink">{m.name}</span>
                <span className="mt-1 block text-sm leading-relaxed text-muted">{m.carga}</span>
              </span>
              <span className="shrink-0 font-display text-xl font-extrabold text-ink tnum">
                R$ {m.price}
              </span>
            </label>
          );
        })}

        {/* O total é o mostrador desta seção, então tem a escala de um mostrador. */}
        <div className="flex flex-wrap items-center justify-between gap-x-8 gap-y-4 bg-paper px-5 py-5">
          <div>
            <p className="font-mono text-[11px] font-medium uppercase tracking-[0.14em] text-muted">
              Sua assinatura
            </p>
            <p aria-live="polite" className="mt-1 flex items-baseline gap-1.5">
              <span className="font-display text-[2.75rem] font-extrabold leading-none tracking-tight text-ink tnum">
                R$ {total}
              </span>
              <span className="text-sm text-muted">/mês</span>
            </p>
            <p className="mt-2 text-sm text-muted">
              {total === 0
                ? "Nenhum módulo ligado: a carteira organizada, de graça, para sempre."
                : `Tudo ligado sai por R$ ${TUDO}.`}
            </p>
          </div>
          <Link
            href="/assinatura"
            className="rounded-md bg-navy px-5 py-3 text-sm font-semibold text-white transition-colors hover:bg-navy/90"
          >
            {total === 0 ? "Começar grátis" : "Assinar"}
          </Link>
        </div>
      </div>

      {/* Fora da lista de propósito: não é um módulo que se liga, é a conta
          inteira multiplicada por cliente. Por isso custa mais que ligar tudo. */}
      <div className="mt-4 flex flex-wrap items-center justify-between gap-x-8 gap-y-3 rounded-lg border border-hairline bg-panel px-5 py-4">
        <div className="min-w-0">
          <p className="text-sm font-semibold text-ink">{BUNDLE.name}</p>
          <p className="mt-1 max-w-[64ch] text-sm leading-relaxed text-muted">
            {BUNDLE.carga}
          </p>
        </div>
        <div className="flex items-center gap-5">
          <p className="font-display text-xl font-extrabold text-ink tnum">
            R$ {BUNDLE.price}
            <span className="ml-1 text-sm font-normal text-muted">/mês</span>
          </p>
          <Link
            href="/assinatura"
            className="rounded-md border border-hairline px-4 py-2.5 text-sm font-semibold text-ink transition-colors hover:bg-hairline/40"
          >
            Assinar
          </Link>
        </div>
      </div>
    </section>
  );
}
