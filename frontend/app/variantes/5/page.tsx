// ── Variação 5 · "declaração" ─────────────────────────────────────────────────
// The loudest of the five: an inverted band carries a three-line statement and a
// single action, then the product appears as four visual tiles. Ink/paper
// inversion rather than navy, so the band survives dark mode. A sticky action bar
// keeps the CTA on screen on phones, where most of this traffic reads.
import { IconScissors } from "@/components/icons";
import { formatMoney } from "@/lib/format";
import { DARF, GANHO } from "@/lib/apuracao";
import {
  AllocationBars,
  Close,
  Cta,
  Footer,
  Nav,
  Pricing,
  ProofBand,
  TRUST,
  TaxedCurve,
} from "../shared";

const TILES = [
  {
    tag: "Carteira",
    title: "Posições consolidadas",
    line: "Preço médio ajustado por eventos, entre corretoras.",
    Visual: () => <AllocationBars />,
  },
  {
    tag: "IRPF",
    title: "DARF e ficha prontas",
    line: "Apuração mensal com isenção e prejuízo compensado.",
    Visual: () => (
      <div className="flex h-full flex-col justify-center rounded-[4px] bg-paper px-4">
        <p className="font-mono text-[11px] uppercase tracking-[0.14em] text-muted">
          Código 6015 · vence 31/08
        </p>
        <p className="font-display text-3xl font-extrabold text-ink tnum">{formatMoney(DARF)}</p>
      </div>
    ),
  },
  {
    tag: "Bench",
    title: "Backtest com imposto",
    line: "A distância entre as curvas é a alíquota, descontada por operação.",
    Visual: () => <TaxedCurve className="h-full w-full" />,
  },
  {
    tag: "Colheita de prejuízo",
    title: "Antes de o mês fechar",
    line: `Acha os perdedores que abatem o ganho de ${formatMoney(GANHO)}.`,
    Visual: () => (
      <div className="flex h-full items-center gap-3 rounded-[4px] bg-paper px-4">
        <IconScissors size={22} className="shrink-0 text-navy" />
        <p className="font-mono text-[11px] leading-snug text-muted">
          − {formatMoney(640)} de prejuízo aplicado à base de julho
        </p>
      </div>
    ),
  },
];

export default function Variante5() {
  return (
    <div className="min-h-screen bg-paper pb-20 sm:pb-0">
      <Nav inverted />

      {/* The statement, at the largest type on the page and nothing else in the
          band competing with it. */}
      <section className="bg-ink text-paper">
        <div className="mx-auto max-w-6xl px-4 py-20 md:px-8 md:py-28">
          <h1 className="max-w-4xl font-display text-[clamp(2.5rem,7vw,5rem)] font-extrabold leading-[1.02] tracking-tight">
            <span className="block">Seu imposto da B3,</span>
            <span className="block">calculado.</span>
            <span className="block text-paper/50">Não estimado.</span>
          </h1>
          <div className="mt-9 flex flex-wrap items-center gap-4">
            <Cta variant="inverted">Começar grátis</Cta>
            <span className="text-sm text-paper/60">{TRUST}</span>
          </div>
        </div>
      </section>

      <main className="mx-auto max-w-6xl px-4 md:px-8">
        {/* Four tiles: what the product is, shown rather than described. */}
        <div className="mt-12 grid gap-4 md:grid-cols-2">
          {TILES.map(({ tag, title, line, Visual }) => (
            <section key={tag} className="rounded-lg border border-hairline bg-panel p-5">
              <p className="font-mono text-[11px] font-medium uppercase tracking-[0.14em] text-muted">
                {tag}
              </p>
              <h2 className="mt-2 font-display text-xl font-extrabold tracking-tight text-ink">
                {title}
              </h2>
              {/* Same floor as the outlets panel: the four allocation bars are
                  the tallest visual, so every tile reserves their height. */}
              <div className="mt-4 h-32">
                <Visual />
              </div>
              <p className="mt-4 text-sm leading-relaxed text-muted">{line}</p>
            </section>
          ))}
        </div>

        <ProofBand className="mt-4" />

        <Pricing compact />
        <Close
          title="Um extrato e você já sabe o número."
          line="O plano grátis organiza a carteira para sempre."
        />
      </main>
      <Footer />

      {/* Phones only: the action follows the reader instead of waiting at the
          bottom of a page they may never reach. */}
      <div className="fixed inset-x-0 bottom-0 z-30 border-t border-hairline bg-paper/95 p-3 backdrop-blur sm:hidden">
        <Cta className="w-full">Começar grátis</Cta>
      </div>
    </div>
  );
}
