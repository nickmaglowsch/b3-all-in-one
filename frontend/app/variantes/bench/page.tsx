// ── Variação bench · "backtest com imposto" ───────────────────────────────────
// The backtest leads. The curve is the hero object at the largest size on the
// page, and the argument is the gap between the two lines: retorno bruto is a
// number that never reaches your account. The DARF still appears — but as the
// resolution, not the pitch, because the same engine produces both and that is
// the one claim a foreign backtester and a Brazilian tax app each only half of.
import { formatPercent } from "@/lib/format";
import { ALIQUOTA } from "@/lib/apuracao";
import {
  BenchCard,
  Close,
  Cta,
  CUSTO_PP,
  DarfCard,
  Footer,
  Nav,
  Outlets,
  Pricing,
  ProofBand,
  TRUST,
} from "../shared";

// Only claims the tier ladder already makes (lib/plans.ts), so the landing does
// not promise a capability the product does not sell.
const MOTOR: [string, string][] = [
  ["Estratégias próprias", "Regras suas, versionadas, rodando sobre o histórico da B3."],
  ["Overlays por regime", "A mesma estratégia sob condições de mercado diferentes."],
  ["Imposto embutido", "Alíquota por mês com ganho, prejuízo compensado, isenção respeitada."],
  ["Sua carteira de verdade", "O backtest parte das posições que os extratos já trouxeram."],
];

export default function VarianteBench() {
  return (
    <div className="min-h-screen bg-paper">
      <Nav />
      <main className="mx-auto max-w-6xl px-4 md:px-8">
        <section className="pt-14 md:pt-20">
          <div className="max-w-3xl">
            <p className="font-mono text-[11px] font-medium uppercase tracking-[0.14em] text-muted">
              Bench · backtest com imposto
            </p>
            <h1 className="mt-4 font-display text-[clamp(2.25rem,5.5vw,3.75rem)] font-extrabold leading-[1.05] tracking-tight text-ink">
              Um backtest que paga imposto.
            </h1>
            <p className="mt-5 max-w-[54ch] text-lg leading-relaxed text-muted">
              Retorno bruto é um número que não chega na sua conta. Entre ele e o seu bolso está a
              alíquota de {formatPercent(ALIQUOTA)} — de cada mês com ganho, com o prejuízo dos meses
              ruins abatido.
            </p>
          </div>

          {/* The hero object is the instrument itself, at the size that lets you
              read the gap rather than infer it. */}
          <BenchCard hero className="mt-10" />

          <p className="mt-4 max-w-[60ch] text-sm leading-relaxed text-ink">
            Neste exemplo o imposto custa <span className="font-semibold tnum">{CUSTO_PP}</span> de
            retorno em dois anos. Nenhum backtest que ignora a alíquota mostra essa linha.
          </p>

          <div className="mt-8 flex flex-wrap items-center gap-3">
            <Cta>Rodar com a minha carteira</Cta>
            <span className="text-sm text-muted">{TRUST}</span>
          </div>
        </section>

        {/* What the engine is, in the terms the ladder already sells. */}
        <dl className="mt-16 grid overflow-hidden rounded-lg border border-hairline bg-panel sm:grid-cols-2">
          {MOTOR.map(([tag, line], i) => (
            <div
              key={tag}
              className={`p-5 ${i > 0 ? "border-t border-hairline" : ""} ${
                i % 2 === 1 ? "sm:border-l" : ""
              } ${i === 1 ? "sm:border-t-0" : ""}`}
            >
              <dt className="font-mono text-[11px] font-medium uppercase tracking-[0.14em] text-muted">
                {tag}
              </dt>
              <dd className="mt-2 text-sm leading-relaxed text-ink">{line}</dd>
            </div>
          ))}
        </dl>

        {/* The resolution: the same alíquota that bent the curve is the one that
            comes out as a payable figure. A backtester cannot do this half and a
            tax app cannot do the other. */}
        <section className="pt-16">
          <div className="grid items-center gap-10 lg:grid-cols-[1fr_22rem] lg:gap-16">
            <div>
              <h2 className="max-w-2xl font-display text-3xl font-extrabold tracking-tight text-ink md:text-4xl">
                O mesmo imposto que curva o backtest é o que você paga.
              </h2>
              <p className="mt-4 max-w-[52ch] text-muted">
                Não são dois cálculos parecidos: é um motor. O que desconta a alíquota mês a mês na
                curva acima é o que apura julho e emite a guia — com a isenção mensal e o prejuízo
                acumulado nas mesmas regras.
              </p>
            </div>
            <DarfCard />
          </div>

          {/* The Bench cell repeats the hero's two figures on purpose: that the
              same number shows up in all three screens IS the claim. The heading
              says so, otherwise it reads as filler. */}
          <h3 className="mt-14 font-display text-2xl font-extrabold tracking-tight text-ink">
            O mesmo número nas três telas
          </h3>
          <Outlets className="mt-5" />
          <ProofBand className="mt-4" />
        </section>

        <Pricing />
        <Close
          title="Teste a estratégia com o imposto ligado."
          line="Um extrato importado e o backtest parte da carteira que você já tem."
        />
      </main>
      <Footer />
    </div>
  );
}
