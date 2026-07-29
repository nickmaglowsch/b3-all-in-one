// ── Variação 2 · "um número" ──────────────────────────────────────────────────
// Figure-first: the DARF at display scale is the hero, and the whole argument is
// a three-node diagram under it. The apuração ledger stays, cut to five rows —
// the evidence, not an essay about the evidence.
import { formatMoney, formatPercent } from "@/lib/format";
import { ALIQUOTA, BASE, CUSTO, DARF, GANHO, PREJUIZO, VENDAS } from "@/lib/apuracao";
import { Close, Cta, Footer, Nav, Outlets, Pricing, ProofBand, TRUST } from "../shared";

const CADEIA: [string, string, boolean?][] = [
  ["Vendas de ações em julho", formatMoney(VENDAS)],
  ["Custo pelo preço médio", `− ${formatMoney(CUSTO)}`],
  ["Ganho apurado", formatMoney(GANHO, true), true],
  ["Prejuízo a compensar", `− ${formatMoney(PREJUIZO)}`],
  ["Base × alíquota", `${formatMoney(BASE)} × ${formatPercent(ALIQUOTA)}`],
];

const NODES = [
  { tag: "Entra", title: "O extrato da B3", line: "2 relatórios, 24 tipos de movimentação." },
  { tag: "Calcula", title: "Um motor de imposto", line: "Isenção, preço médio, prejuízo, eventos." },
  { tag: "Sai", title: "Três telas", line: "Bench, carteira e IRPF leem o mesmo número." },
];

export default function Variante2() {
  return (
    <div className="min-h-screen bg-paper">
      <Nav />
      <main className="mx-auto max-w-6xl px-4 md:px-8">
        <section className="py-16 md:py-24">
          <div className="grid gap-10 lg:grid-cols-[1fr_1fr] lg:items-center lg:gap-16">
            <div>
              <h1 className="font-display text-[clamp(2.25rem,5vw,3.5rem)] font-extrabold leading-[1.05] tracking-tight text-ink">
                Você deve exatamente isto:
              </h1>
              <p className="mt-5 max-w-[44ch] text-lg leading-relaxed text-muted">
                Calculado dos seus extratos da B3, não digitado numa planilha.
              </p>
              <div className="mt-8 flex flex-wrap items-center gap-3">
                <Cta>Calcular o meu</Cta>
                <span className="text-sm text-muted">{TRUST}</span>
              </div>
            </div>

            {/* The figure gets the display step the design system reserves for
                landing numbers — it is the product's entire output. */}
            <div className="rounded-lg border border-hairline bg-panel p-6 md:p-8">
              <div className="flex items-baseline justify-between gap-3">
                <p className="font-mono text-[11px] font-medium uppercase tracking-[0.14em] text-muted">
                  DARF · código 6015 · vence 31/08
                </p>
                <span className="shrink-0 rounded-[4px] border border-hairline bg-paper px-1.5 py-0.5 font-mono text-[11px] font-medium uppercase leading-none tracking-wide text-muted">
                  exemplo
                </span>
              </div>
              <p className="mt-3 font-display text-[clamp(3rem,9vw,5.5rem)] font-extrabold leading-none tracking-tight text-ink tnum">
                {formatMoney(DARF)}
              </p>
              <dl className="mt-6 border-t border-hairline pt-4">
                {CADEIA.map(([label, value, strong]) => (
                  <div
                    key={label}
                    className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1 py-1.5 text-sm"
                  >
                    <dt className={strong ? "font-semibold text-ink" : "text-muted"}>{label}</dt>
                    <dd
                      className={`shrink-0 tnum ${strong ? "font-semibold text-gain" : "text-ink"}`}
                    >
                      {value}
                    </dd>
                  </div>
                ))}
              </dl>
            </div>
          </div>
        </section>

        {/* One motor, three saídas — as a diagram with arrows, which is the
            claim the old page spent four scrolling stages to make. */}
        <section className="pb-4">
          <div className="grid items-stretch gap-3 md:grid-cols-[1fr_auto_1fr_auto_1fr] md:gap-0">
            {NODES.map((n, i) => (
              <div key={n.tag} className="contents">
                <div
                  className={`rounded-lg border border-hairline p-5 ${
                    i === 1 ? "bg-navy/[0.05]" : "bg-panel"
                  }`}
                >
                  <p className="font-mono text-[11px] font-medium uppercase tracking-[0.14em] text-muted">
                    {n.tag}
                  </p>
                  <h2 className="mt-2 text-base font-semibold text-ink">{n.title}</h2>
                  <p className="mt-1 text-sm leading-relaxed text-muted">{n.line}</p>
                </div>
                {i < 2 && (
                  <div
                    aria-hidden
                    className="flex items-center justify-center py-1 md:px-3 md:py-0"
                  >
                    <span className="font-mono text-lg text-axis">→</span>
                  </div>
                )}
              </div>
            ))}
          </div>
        </section>

        <Outlets className="mt-8" />
        <ProofBand className="mt-4" />

        <Pricing />
        <Close
          title="Um cálculo, três telas."
          line="Importe um extrato e veja o seu número em um minuto."
        />
      </main>
      <Footer />
    </div>
  );
}
