// ── Variação 1 · "produto na cara" ───────────────────────────────────────────
// Split hero: claim + actions left, the app's own readouts right, both above the
// fold. Then three steps as a strip, one figure band, concise pricing. Zero
// paragraphs longer than two lines — the shortest read of the five.
import { IconReceipt, IconUpload, IconWallet } from "@/components/icons";
import { formatMoney } from "@/lib/format";
import { DARF } from "@/lib/apuracao";
import {
  CarteiraCard,
  Close,
  Cta,
  DarfCard,
  Footer,
  Nav,
  Pricing,
  ProofBand,
  TRUST,
} from "../shared";

const PASSOS = [
  {
    Icon: IconUpload,
    title: "Suba os extratos",
    line: "Os dois relatórios da Área do Investidor, como vêm.",
  },
  {
    Icon: IconWallet,
    title: "Veja a carteira",
    line: "Preço médio, proventos e resultado, já ajustados por eventos.",
  },
  {
    Icon: IconReceipt,
    title: "Pague a DARF",
    line: "Apuração mensal com isenção e prejuízo, código 6015.",
  },
];

export default function Variante1() {
  return (
    <div className="min-h-screen bg-paper">
      <Nav />
      <main className="mx-auto max-w-6xl px-4 md:px-8">
        <section className="grid items-center gap-12 py-16 md:py-24 lg:grid-cols-[1fr_28rem] lg:gap-16">
          <div>
            <p className="font-mono text-[11px] font-medium uppercase tracking-[0.14em] text-muted">
              Carteira e imposto da B3
            </p>
            <h1 className="mt-4 font-display text-[clamp(2.25rem,5.5vw,4rem)] font-extrabold leading-[1.05] tracking-tight text-ink">
              Do extrato da B3 à DARF em um minuto.
            </h1>
            <p className="mt-5 max-w-[46ch] text-lg leading-relaxed text-muted">
              Sobe os relatórios, o resto é calculado: carteira, imposto do mês e a ficha do IRPF.
            </p>
            <div className="mt-8 flex flex-wrap items-center gap-3">
              <Cta>Começar grátis</Cta>
              <Cta href="#planos" variant="quiet">
                Ver os planos
              </Cta>
            </div>
            <p className="mt-4 text-sm text-muted">{TRUST}</p>
          </div>

          {/* The readouts, stacked: what you get, before any explanation of how. */}
          <div className="space-y-4">
            <CarteiraCard />
            <DarfCard />
          </div>
        </section>

        {/* Three steps as one strip — the whole "como funciona" section, in 30
            words instead of three scrolls. */}
        <ol className="grid overflow-hidden rounded-lg border border-hairline bg-panel md:grid-cols-3">
          {PASSOS.map(({ Icon, title, line }, i) => (
            <li
              key={title}
              className={`p-5 ${i > 0 ? "border-t border-hairline md:border-l md:border-t-0" : ""}`}
            >
              <div className="flex items-center gap-2.5">
                <Icon size={18} className="shrink-0 text-navy" />
                <p className="font-mono text-[11px] font-medium uppercase tracking-[0.14em] text-muted">
                  Passo {i + 1}
                </p>
              </div>
              <h2 className="mt-3 text-base font-semibold text-ink">{title}</h2>
              <p className="mt-1 text-sm leading-relaxed text-muted">{line}</p>
            </li>
          ))}
        </ol>

        <ProofBand className="mt-4" />

        <Pricing />
        <Close
          title={`Seu julho custaria ${formatMoney(DARF)}.`}
          line="O app diz o número; você decide o que fazer com ele."
        />
      </main>
      <Footer />
    </div>
  );
}
