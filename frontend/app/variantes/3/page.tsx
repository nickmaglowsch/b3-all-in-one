// ── Variação 3 · "aposente a planilha" ────────────────────────────────────────
// Frames the product against what the reader is doing today. Hero is a
// side-by-side of the two workflows; the body is one comparison table plus the
// three outlets. Copy carries the contrast, so almost no prose is needed.
import { IconCheck, IconClose } from "@/components/icons";
import { formatMoney } from "@/lib/format";
import { DARF, GANHO } from "@/lib/apuracao";
import {
  CarteiraCard,
  Close,
  Cta,
  Footer,
  Nav,
  Outlets,
  Pricing,
  TRUST,
} from "../shared";

const LINHAS: [string, boolean][] = [
  ["Preço médio ajustado por desdobro, bonificação e amortização", true],
  ["Isenção de R$ 20.000 conferida mês a mês", true],
  ["Prejuízo acumulado compensado por classe", true],
  ["Proventos e JCP conciliados com o extrato", true],
  ["DARF pronta, com código e vencimento", true],
  ["Backtest que desconta o imposto que você pagaria", true],
];

export default function Variante3() {
  return (
    <div className="min-h-screen bg-paper">
      <Nav />
      <main className="mx-auto max-w-6xl px-4 md:px-8">
        <section className="py-16 md:py-24">
          <h1 className="max-w-3xl font-display text-[clamp(2.25rem,5.5vw,4rem)] font-extrabold leading-[1.05] tracking-tight text-ink">
            Aposente a planilha de imposto.
          </h1>
          <p className="mt-5 max-w-[52ch] text-lg leading-relaxed text-muted">
            O mesmo trabalho de um fim de semana por trimestre, feito a partir dos extratos da B3.
          </p>
          <div className="mt-8 flex flex-wrap items-center gap-3">
            <Cta>Começar grátis</Cta>
            <span className="text-sm text-muted">{TRUST}</span>
          </div>

          {/* The two workflows side by side: the left column is deliberately
              plain paper and grey, the right one is a live readout. */}
          <div className="mt-14 grid gap-4 lg:grid-cols-2 lg:gap-6">
            <div className="rounded-lg border border-dashed border-axis p-5">
              <p className="font-mono text-[11px] font-medium uppercase tracking-[0.14em] text-muted">
                Hoje · planilha
              </p>
              <ul className="mt-4 space-y-3 text-sm text-muted">
                {[
                  "Copia e cola dois relatórios por mês",
                  "Recalcula o preço médio a cada desdobro",
                  "Confere a isenção de R$ 20.000 na mão",
                  "Descobre o prejuízo compensável em abril",
                ].map((l) => (
                  <li key={l} className="flex items-start gap-2.5">
                    <IconClose size={16} className="mt-0.5 shrink-0 text-axis" />
                    <span>{l}</span>
                  </li>
                ))}
              </ul>
              <p className="mt-6 border-t border-hairline pt-4 font-display text-2xl font-extrabold tracking-tight text-muted">
                3 horas por mês
              </p>
            </div>

            <div className="space-y-4">
              <CarteiraCard />
              <div className="flex flex-wrap items-baseline justify-between gap-3 rounded-lg border border-hairline bg-navy/[0.05] px-5 py-4">
                <div>
                  <p className="font-mono text-[11px] font-medium uppercase tracking-[0.14em] text-muted">
                    Com o Quantax · DARF de julho
                  </p>
                  <p className="mt-1 font-display text-3xl font-extrabold tracking-tight text-ink tnum">
                    {formatMoney(DARF)}
                  </p>
                </div>
                <p className="font-display text-xl font-extrabold tracking-tight text-navy">
                  1 minuto
                </p>
              </div>
            </div>
          </div>
        </section>

        {/* What the planilha silently gets wrong, as a checklist rather than six
            paragraphs of tax explanation. */}
        <section className="border-t border-hairline py-12 md:py-16">
          <h2 className="font-display text-3xl font-extrabold tracking-tight text-ink">
            O que a planilha erra calado
          </h2>
          <ul className="mt-8 grid gap-x-10 gap-y-4 md:grid-cols-2">
            {LINHAS.map(([l]) => (
              <li
                key={l}
                className="flex items-start gap-2.5 border-b border-hairline/70 pb-4 text-sm text-ink"
              >
                <IconCheck size={18} className="mt-0.5 shrink-0 text-navy" />
                <span>{l}</span>
              </li>
            ))}
          </ul>
          <p className="mt-6 text-sm text-muted">
            O ganho de {formatMoney(GANHO)} sai do mesmo cálculo nas três telas abaixo.
          </p>
          <Outlets className="mt-6" />
        </section>

        <Pricing compact />
        <Close
          title="Sua carteira da B3, sem planilha."
          line="Leva um minuto para importar o primeiro extrato."
        />
      </main>
      <Footer />
    </div>
  );
}
