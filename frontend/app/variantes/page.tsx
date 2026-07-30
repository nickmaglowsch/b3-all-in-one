// Review index for the surviving landing variations. Not a marketing page — a
// switch board, so the two can be compared side by side before one replaces `/`.
// Três das cinco foram descartadas: 1 e 5 reinstalavam a grade de cartões com
// ícone que a landing atual existe para recusar, e 3 apoiava o argumento inteiro
// em figuras de tempo que nada mede.
import Link from "next/link";

import { Brand } from "@/components/Brand";

const VARIANTES: [string, string, string][] = [
  [
    "bench",
    "Backtest com imposto",
    "O backtest é o herói: a curva bruta contra a líquida em escala grande, e o argumento é a distância entre as duas. A DARF aparece como desfecho, não como pitch.",
  ],
  [
    "2",
    "Um número",
    "A DARF em escala de display é o herói, com a cadeia da apuração ao lado — cinco linhas que fecham no próprio número. Depois as três saídas e o readout do Bench.",
  ],
  [
    "4",
    "Calculadora",
    "O herói é interativo: dois sliders e o imposto que eles produzem, com a isenção desenhada como uma travessia. Abre dentro da isenção, então o primeiro arrasto é a descoberta.",
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
          As que sobreviveram à crítica, mais uma com o backtest em foco — todas mais curtas e mais
          visuais que a atual, com o mesmo exemplo de julho de 2026 e o mesmo sistema de design.
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
