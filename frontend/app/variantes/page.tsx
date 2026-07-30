// Review index for the candidate landing. Not a marketing page — a switch board,
// so it can be compared against `/` before replacing it.
//
// Cinco variações entraram, uma sobrou. 1 e 5 reinstalavam a grade de cartões com
// ícone que a landing atual existe para recusar; 3 apoiava o argumento em figuras
// de tempo que nada mede; 2, 4 e bench eram boas, mas cada uma defendia um pedaço
// (um número, uma calculadora, um backtest) — e a tese é que os pedaços são um
// motor. Quatro landings competindo é artefato de review, não entrega: o que cada
// uma provava melhor virou seção desta.
import Link from "next/link";

import { Brand } from "@/components/Brand";

const VARIANTES: [string, string, string][] = [
  [
    "tudo",
    "Um lugar para tudo na B3",
    "Um painel só: a faixa de entrada atravessa a largura e as quatro leituras — carteira, imposto, estratégias, clientes — ficam embaixo dela. Depois a curva bruta contra a líquida, que é a prova de que é um motor, e a assinatura montada módulo a módulo.",
  ],
];

export default function VariantesIndex() {
  return (
    <div className="min-h-screen bg-paper">
      <main className="mx-auto max-w-3xl px-4 py-16 md:px-8">
        <Brand className="text-lg" />
        <h1 className="mt-8 font-display text-3xl font-extrabold tracking-tight text-ink">
          Candidata a landing
        </h1>
        <p className="mt-3 text-muted">
          Uma só, depois de cinco: mais curta e mais visual que a atual, com o mesmo exemplo de julho
          de 2026 e o mesmo sistema de design.
        </p>

        <ul className="mt-10 overflow-hidden rounded-lg border border-hairline bg-panel">
          {VARIANTES.map(([n, name, desc], i) => (
            <li key={n} className={i > 0 ? "border-t border-hairline" : ""}>
              <Link
                href={`/variantes/${n}`}
                className="flex gap-5 p-5 transition-colors hover:bg-hairline/30"
              >
                <span className="w-14 shrink-0 font-mono text-sm font-medium uppercase tracking-wide text-navy tnum">
                  {n}
                </span>
                <span className="min-w-0">
                  <span className="block text-base font-semibold text-ink">{name}</span>
                  <span className="mt-1 block text-sm leading-relaxed text-muted">{desc}</span>
                </span>
              </Link>
            </li>
          ))}
          <li className="border-t border-hairline">
            <Link
              href="/"
              className="flex gap-5 p-5 transition-colors hover:bg-hairline/30"
            >
              <span className="font-mono text-sm text-muted">/</span>
              <span>
                <span className="block text-base font-semibold text-ink">Atual</span>
                <span className="mt-1 block text-sm text-muted">
                  A landing em produção, para comparar.
                </span>
              </span>
            </Link>
          </li>
        </ul>
      </main>
    </div>
  );
}
