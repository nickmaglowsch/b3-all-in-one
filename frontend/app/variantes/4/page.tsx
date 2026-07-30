// ── Variação 4 · "calculadora" ────────────────────────────────────────────────
// The hero is a thing you use, not a thing you read: two sliders and the DARF
// they produce. Everything after it is one line per claim, because the reader has
// already seen the product work.
import {
  BenchCard,
  Close,
  Cta,
  Footer,
  Nav,
  Outlets,
  Pricing,
  ProofBand,
  quadEdges,
  TRUST,
} from "../shared";
import { Calculadora } from "./Calculadora";

// Bench is not in this strip: it gets its own readout further down, because a
// one-liner is what left it looking like a checklist item.
const FEATURES: [string, string][] = [
  ["Importação", "Os dois relatórios da B3 entram inteiros — 24 tipos de movimentação."],
  ["Carteira", "Preço médio ajustado por desdobro, bonificação e amortização."],
  ["Colheita de prejuízo", "Acha os perdedores que derrubam a base antes do mês fechar."],
  ["IRPF", "Ficha de renda variável e DARF 6015, mês a mês, com o prejuízo compensado."],
];

export default function Variante4() {
  return (
    <div className="min-h-screen bg-paper">
      <Nav />
      <main className="mx-auto max-w-6xl px-4 md:px-8">
        <section className="py-14 md:py-20">
          <div className="max-w-3xl">
            <h1 className="font-display text-[clamp(2.25rem,5.5vw,3.75rem)] font-extrabold leading-[1.05] tracking-tight text-ink">
              Quanto de imposto você deve este mês?
            </h1>
            <p className="mt-5 max-w-[52ch] text-lg leading-relaxed text-muted">
              Arraste para ver a regra. Depois deixe o app fazer isso com os seus extratos.
            </p>
          </div>

          <div className="mt-10">
            <Calculadora />
          </div>

          <div className="mt-8 flex flex-wrap items-center gap-3">
            <Cta>Calcular com meu extrato</Cta>
            <span className="text-sm text-muted">{TRUST}</span>
          </div>
        </section>

        {/* Four claims, one line each. */}
        <dl className="grid overflow-hidden rounded-lg border border-hairline bg-panel sm:grid-cols-2 xl:grid-cols-4">
          {FEATURES.map(([tag, line], i) => (
            <div
              key={tag}
              className={`p-5 ${quadEdges(i)}`}
            >
              <dt className="font-mono text-[11px] font-medium uppercase tracking-[0.14em] text-muted">
                {tag}
              </dt>
              <dd className="mt-2 text-sm leading-relaxed text-ink">{line}</dd>
            </div>
          ))}
        </dl>

        <section className="pt-12">
          <h2 className="max-w-2xl font-display text-3xl font-extrabold tracking-tight text-ink">
            O mesmo número nas três telas
          </h2>
          <Outlets className="mt-6" />
          <ProofBand className="mt-4" />
        </section>

        {/* The slider taught one month. This is the same rule over a série: what
            the imposto costs a strategy, with the figures whoever backtests reads
            before the retorno. */}
        <section className="pt-12">
          <h2 className="max-w-2xl font-display text-3xl font-extrabold tracking-tight text-ink">
            E o que o imposto custa a uma estratégia
          </h2>
          <BenchCard className="mt-6" />
        </section>

        <Pricing />
        <Close
          title="Pare de estimar o imposto."
          line="Um extrato importado e o número deixa de ser um chute."
        />
      </main>
      <Footer />
    </div>
  );
}
