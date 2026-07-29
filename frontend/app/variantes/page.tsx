// Review index for the five landing variations. Not a marketing page — a switch
// board, so all five can be compared side by side before one replaces `/`.
import Link from "next/link";

import { Brand } from "@/components/Brand";

const VARIANTES: [string, string, string][] = [
  [
    "1",
    "Produto na cara",
    "Hero dividido: promessa e ações à esquerda, carteira e DARF à direita. Três passos numa faixa, banda de provas, planos.",
  ],
  [
    "2",
    "Um número",
    "A DARF em escala de display é o herói, com a cadeia da apuração ao lado. Diagrama de três nós e as três saídas.",
  ],
  [
    "3",
    "Aposente a planilha",
    "Compara o fluxo de hoje com o do app lado a lado, depois lista o que a planilha erra calado.",
  ],
  [
    "4",
    "Calculadora",
    "O herói é interativo: dois sliders e a DARF que eles produzem, com a isenção desenhada.",
  ],
  [
    "5",
    "Declaração",
    "Faixa invertida com uma frase em três linhas e uma ação. O produto aparece como quatro azulejos visuais, CTA fixo no mobile.",
  ],
];

export default function VariantesIndex() {
  return (
    <div className="min-h-screen bg-paper">
      <main className="mx-auto max-w-3xl px-4 py-16 md:px-8">
        <Brand className="text-lg" />
        <h1 className="mt-8 font-display text-3xl font-extrabold tracking-tight text-ink">
          Variações da landing
        </h1>
        <p className="mt-3 text-muted">
          Cinco versões mais curtas e mais visuais que a atual. Todas usam o mesmo exemplo de julho
          de 2026 e o mesmo sistema de design.
        </p>

        <ul className="mt-10 overflow-hidden rounded-lg border border-hairline bg-panel">
          {VARIANTES.map(([n, name, desc], i) => (
            <li key={n} className={i > 0 ? "border-t border-hairline" : ""}>
              <Link
                href={`/variantes/${n}`}
                className="flex gap-5 p-5 transition-colors hover:bg-hairline/30"
              >
                <span className="font-display text-2xl font-extrabold tracking-tight text-navy tnum">
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
