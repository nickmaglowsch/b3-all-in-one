// Chrome shared by the two surviving landing variations, so each variant file
// holds only what makes it different: hero form, proof form, order of the
// argument. Same design system as the app (DESIGN.md): Bench Paper ground, Panel
// White surfaces, hairlines doing the separation, scarce navy, tnum on figures.
import Link from "next/link";

import { Brand } from "@/components/Brand";
import { ALIQUOTA, DARF, GANHO, linePath, serieLiquida } from "@/lib/apuracao";
import { summarize } from "@/lib/chartRange";
import { formatMoney, formatPercent } from "@/lib/format";
import { PLANS } from "@/lib/plans";

export function Nav() {
  return (
    <header className="sticky top-0 z-20 border-b border-hairline bg-paper/85 text-ink backdrop-blur">
      <div className="mx-auto flex h-16 max-w-6xl items-center justify-between px-4 md:px-8">
        <Brand className="text-lg" />
        <nav className="flex items-center gap-4 sm:gap-6">
          <a
            href="#planos"
            className="hidden py-2 text-sm font-medium text-muted transition-colors hover:text-ink sm:block"
          >
            Planos
          </a>
          <Link
            href="/login"
            className="py-2 text-sm font-medium text-muted transition-colors hover:text-ink"
          >
            Entrar
          </Link>
          <Link
            href="/login"
            className="rounded-md bg-navy px-4 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-navy/90"
          >
            Começar grátis
          </Link>
        </nav>
      </div>
    </header>
  );
}

export function Cta({
  children,
  href = "/login",
  className = "",
}: {
  children: React.ReactNode;
  href?: string;
  className?: string;
}) {
  return (
    <Link
      href={href}
      className={`inline-block rounded-md border border-transparent bg-navy px-5 py-3 text-center text-sm font-semibold text-white transition-colors hover:bg-navy/90 ${className}`}
    >
      {children}
    </Link>
  );
}

export const TRUST = "Sem cartão. Só leitura — nada executa na sua corretora.";

// The chip that marks every figure on these pages as synthetic. One component so
// no readout can be added without the marking (PRODUCT.md: never a number that
// looks like the reader's own).
export function ExemploChip({ children = "exemplo" }: { children?: string }) {
  return (
    <span className="shrink-0 rounded-[4px] border border-hairline bg-paper px-1.5 py-0.5 font-mono text-[11px] font-medium uppercase leading-none tracking-wide text-muted">
      {children}
    </span>
  );
}

// The figure the same engine produces at the other end of the loop. On the bench
// variant this is the resolution, not the hero: the backtest earns the attention
// and the DARF is what the attention pays off in.
export function DarfCard({ className = "" }: { className?: string }) {
  return (
    <div className={`rounded-lg border border-hairline bg-panel p-5 ${className}`}>
      <div className="flex items-baseline justify-between gap-3">
        <p className="font-mono text-[11px] font-medium uppercase tracking-[0.14em] text-muted">
          DARF · código 6015
        </p>
        <ExemploChip />
      </div>
      <p className="mt-2 font-display text-4xl font-extrabold tracking-tight text-ink tnum">
        {formatMoney(DARF)}
      </p>
      <p className="mt-1 text-sm text-muted">Julho de 2026 · vence 31/08</p>
    </div>
  );
}

// Bruto vs depois do imposto, on one scale. The gap IS the alíquota. Two years of
// monthly closes with a real drawdown in them — a curve that only goes up is not
// a backtest, and the drawdown is where the prejuízo rule becomes visible.
const BRUTO = [
  100, 103, 101, 106, 110, 108, 104, 96, 99, 105, 111, 114, 112, 118, 123, 121, 127, 132, 129, 135,
  140, 138, 144, 148,
];
const LIQUIDO = serieLiquida(BRUTO);

export function TaxedCurve({ className = "" }: { className?: string }) {
  const all = [...BRUTO, ...LIQUIDO];
  const lo = Math.min(...all);
  const hi = Math.max(...all);
  return (
    <svg viewBox="0 0 100 40" preserveAspectRatio="none" className={className} aria-hidden>
      <path
        d={linePath(BRUTO, lo, hi)}
        fill="none"
        stroke="var(--axis)"
        strokeWidth={1.4}
        strokeDasharray="3 3"
        vectorEffect="non-scaling-stroke"
      />
      {/* No `draw-line`: that class only animates under an `.is-visible`
          ancestor, which the incumbent page's scroll observer sets and these
          pages do not — so it left the curve stuck at dashoffset 1, drawn in
          pieces. A static line is the honest version of an animation nothing
          triggers. */}
      <path
        d={linePath(LIQUIDO, lo, hi)}
        fill="none"
        stroke="var(--series-1)"
        strokeWidth={1.8}
        strokeLinecap="round"
        strokeLinejoin="round"
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  );
}

// The Bench readout the sparkline cell only gestures at: the same two curves plus
// the figures someone who backtests actually reads. Every row comes out of
// `summarize` — the same port of the engine's own metric that the Bench screen
// uses — so the landing cannot quote a figure the app would compute differently.
// 24 months, so the CAGR is above summarize's annualisation floor and shows.
const ANOS = 2;
const BENCH_BRUTO = summarize(BRUTO, ANOS);
const BENCH_LIQUIDO = summarize(LIQUIDO, ANOS);
const asPct = (v: number | null) => (v === null ? "—" : formatPercent(v / 100));
const METRICAS: [string, string, string?][] = [
  ["Retorno bruto", asPct(BENCH_BRUTO.total_return_pct)],
  ["Depois do imposto", asPct(BENCH_LIQUIDO.total_return_pct), "gain"],
  ["Ao ano, líquido", asPct(BENCH_LIQUIDO.ann_return_pct)],
  // Da série líquida, não da bruta: é a queda que o dinheiro de verdade viveu, e
  // deixar isso implícito num painel que um quant lê de perto é o tipo de
  // ambiguidade que desqualifica o resto dos números.
  ["Maior queda · líquido", asPct(BENCH_LIQUIDO.max_dd_pct), "loss"],
];

// What the alíquota costs the strategy, in percentage points of total return —
// the one figure this whole panel exists to produce, so it is derived here rather
// than typed into a headline.
export const CUSTO_PP = formatPercent(
  ((BENCH_BRUTO.total_return_pct ?? 0) - (BENCH_LIQUIDO.total_return_pct ?? 0)) / 100,
);

export function BenchCard({ className = "", hero = false }: { className?: string; hero?: boolean }) {
  return (
    <div
      className={`grid overflow-hidden rounded-lg border border-hairline bg-panel lg:grid-cols-[1fr_16rem] ${className}`}
    >
      <div className="p-5 md:p-6">
        <div className="flex items-baseline justify-between gap-3">
          <p className="font-mono text-[11px] font-medium uppercase tracking-[0.14em] text-muted">
            Bench · 24 meses · mensal
          </p>
          <ExemploChip />
        </div>
        <TaxedCurve className={`mt-4 w-full ${hero ? "h-56 md:h-72" : "h-32"}`} />
        {/* The legend names which curve is which, because a dashed line and a
            solid line are not self-describing. The swatch carries the data
            colour; the label stays interface ink. */}
        <div className="mt-3 flex flex-wrap gap-x-6 gap-y-1 font-mono text-[11px] uppercase tracking-[0.12em] text-muted">
          <span className="flex items-center gap-2">
            <span aria-hidden className="w-4 border-t border-dashed border-axis" />
            bruto
          </span>
          <span className="flex items-center gap-2 text-ink">
            <span aria-hidden className="h-[2px] w-4" style={{ background: "var(--series-1)" }} />
            depois do imposto
          </span>
        </div>
      </div>
      <dl className="border-t border-hairline bg-paper p-5 md:p-6 lg:border-l lg:border-t-0">
        {METRICAS.map(([label, value, tone], i) => (
          <div key={label} className={i > 0 ? "mt-4" : ""}>
            <dt className="font-mono text-[11px] uppercase tracking-[0.12em] text-muted">
              {label}
            </dt>
            <dd
              className={`font-display text-2xl font-extrabold tracking-tight tnum ${
                tone === "gain" ? "text-gain" : tone === "loss" ? "text-loss" : "text-ink"
              }`}
            >
              {value}
            </dd>
          </div>
        ))}
        <p className="mt-5 border-t border-hairline pt-4 text-xs leading-relaxed text-muted">
          Contra IBOV e CDI, das séries da própria B3. A alíquota sai de cada mês com ganho, e um mês
          negativo abate os seguintes — por isso a recuperação da queda vem sem imposto.
        </p>
      </dl>
    </div>
  );
}

export const ALOCACAO = [
  { label: "Ações", pct: 0.55, color: "var(--series-1)" },
  { label: "FIIs", pct: 0.25, color: "var(--series-3)" },
  { label: "Renda fixa", pct: 0.15, color: "var(--series-4)" },
  { label: "Caixa", pct: 0.05, color: "var(--series-2)" },
];

export function AllocationBars({ className = "" }: { className?: string }) {
  return (
    <dl className={`space-y-2.5 ${className}`}>
      {ALOCACAO.map((b) => (
        <div key={b.label}>
          <div className="mb-1 flex justify-between text-xs">
            <dt className="text-muted">{b.label}</dt>
            <dd className="tnum text-ink">{formatPercent(b.pct)}</dd>
          </div>
          <div aria-hidden className="h-1.5 overflow-hidden rounded-full bg-hairline/60">
            <div
              className="h-full rounded-full"
              style={{ width: `${b.pct * 100}%`, backgroundColor: b.color }}
            />
          </div>
        </div>
      ))}
    </dl>
  );
}

// The three screens the same apurado figure feeds, as one instrument split by
// hairlines rather than three floating cards.
export const OUTLETS = [
  {
    tag: "Bench",
    line: `O backtest desconta os ${formatPercent(ALIQUOTA)} de cada mês com ganho.`,
    // Two figures, not the curve: BenchCard already shows the curve further down
    // the same scroll, and the gap between these two numbers IS the alíquota.
    Visual: () => (
      <dl className="flex h-full flex-col justify-center gap-3 rounded-[4px] bg-paper px-3">
        {METRICAS.slice(0, 2).map(([label, value, tone]) => (
          <div key={label}>
            <dt className="font-mono text-[11px] uppercase tracking-[0.14em] text-muted">
              {label}
            </dt>
            <dd
              className={`font-display text-xl font-extrabold tnum ${
                tone === "gain" ? "text-gain" : "text-ink"
              }`}
            >
              {value}
            </dd>
          </div>
        ))}
      </dl>
    ),
  },
  {
    tag: "Carteira",
    line: `O rebalanceamento sabe que a venda realiza ${formatMoney(GANHO)}.`,
    Visual: () => <AllocationBars />,
  },
  {
    tag: "IRPF",
    line: "A ficha de renda variável já vem com o mesmo ganho.",
    Visual: () => (
      <div className="flex h-full flex-col items-start justify-center gap-1 rounded-[4px] bg-paper px-3">
        <p className="font-mono text-[11px] uppercase tracking-[0.14em] text-muted">DARF 6015</p>
        <p className="font-display text-2xl font-extrabold text-ink tnum">{formatMoney(DARF)}</p>
        <ExemploChip />
      </div>
    ),
  },
];

export function Outlets({ className = "" }: { className?: string }) {
  return (
    <div
      className={`grid overflow-hidden rounded-lg border border-hairline bg-panel md:grid-cols-3 ${className}`}
    >
      {OUTLETS.map(({ tag, line, Visual }, i) => (
        <div
          key={tag}
          className={`flex flex-col gap-4 p-5 ${
            i > 0 ? "border-t border-hairline md:border-l md:border-t-0" : ""
          }`}
        >
          <p className="font-mono text-[11px] font-medium uppercase tracking-[0.14em] text-muted">
            {tag}
          </p>
          {/* h-32 is the tallest visual (the four allocation bars); the other two
              stretch into it, so the three panels' bands sit on the same lines. */}
          <div className="h-32">
            <Visual />
          </div>
          <p className="mt-auto text-sm leading-relaxed text-ink">{line}</p>
        </div>
      ))}
    </div>
  );
}

// Hairlines for a 4-cell panel that is 1 column on phones, 2 up from sm and 4 up
// from xl — the only separation this system uses, so the rule lives in one place
// instead of being re-derived per grid.
export const quadEdges = (i: number) =>
  [
    i > 0 && "border-t border-hairline sm:border-t-0 xl:border-l",
    i === 1 && "sm:border-l",
    i >= 2 && "sm:border-t",
    i === 3 && "sm:border-l",
  ]
    .filter(Boolean)
    .join(" ");

// Concise pricing: name, price, one line, one CTA. The full feature ladder lives
// on /assinatura — a landing page that lists 18 bullets is not selling, it is
// negotiating with itself.
export function Pricing({ compact = false }: { compact?: boolean }) {
  return (
    <section id="planos" className="scroll-mt-20 border-t border-hairline py-16 md:py-20">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <h2 className="font-display text-3xl font-extrabold tracking-tight text-ink">
          Preço simples, em reais
        </h2>
        <p className="text-sm text-muted">Comece de graça. Cancele quando quiser.</p>
      </div>

      <div className="mt-8 grid overflow-hidden rounded-lg border border-hairline bg-panel sm:grid-cols-2 xl:grid-cols-4">
        {PLANS.map((plan, i) => (
          <div
            key={plan.id}
            className={`flex flex-col p-5 ${quadEdges(i)} ${
              plan.featured ? "bg-navy/[0.03]" : ""
            }`}
          >
            {/* No "mais popular" badge: the product is pre-launch, so there is
                no popularity to report. The tint alone marks the tier. */}
            <h3 className="text-sm font-semibold text-ink">{plan.name}</h3>
            <p className="mt-3 flex items-baseline gap-1">
              <span className="font-display text-3xl font-extrabold tracking-tight text-ink tnum">
                {plan.price}
              </span>
              <span className="text-sm text-muted">{plan.cadence}</span>
            </p>
            {/* One differentiator per tier, not the whole ladder. Two-line floor
                so the four CTAs land on the same line. */}
            <p className="mt-3 flex-1 text-sm leading-relaxed text-muted sm:min-h-[2.625rem]">
              {compact ? plan.tagline : plan.features[i === 0 ? 0 : 1]}
            </p>
            <Link
              href={plan.id === "gratis" ? "/login" : "/assinatura"}
              className={`mt-5 rounded-md border px-4 py-2.5 text-center text-sm font-semibold transition-colors ${
                plan.featured
                  ? "border-transparent bg-navy text-white hover:bg-navy/90"
                  : "border-hairline text-ink hover:bg-hairline/40"
              }`}
            >
              {plan.cta}
            </Link>
          </div>
        ))}
      </div>
    </section>
  );
}

export function Close({ title, line }: { title: string; line: string }) {
  return (
    <section className="border-t border-hairline py-16 md:py-24">
      <h2 className="max-w-2xl font-display text-3xl font-extrabold tracking-tight text-ink md:text-4xl">
        {title}
      </h2>
      <p className="mt-3 max-w-xl text-muted">{line}</p>
      <div className="mt-7 flex flex-wrap items-center gap-3">
        <Cta>Começar grátis</Cta>
        <span className="text-sm text-muted">{TRUST}</span>
      </div>
    </section>
  );
}

export function Footer() {
  return (
    <footer className="border-t border-hairline">
      <div className="mx-auto flex max-w-6xl flex-col gap-4 px-4 py-8 md:flex-row md:items-center md:justify-between md:px-8">
        <Brand className="text-base" />
        <p className="max-w-md text-xs leading-relaxed text-muted">
          Ferramenta de organização de carteira e apuração. Não é recomendação de investimento nem
          serviço de contabilidade. Os números desta página são exemplos.
        </p>
        <Link
          href="/privacidade"
          className="text-xs text-muted underline underline-offset-2 hover:text-ink"
        >
          Política de Privacidade
        </Link>
      </div>
    </footer>
  );
}

// Three claims traceable to the parser, as a figure band. Numbers do the talking
// where the current page spends two paragraphs. Every figure here is countable in
// the code — no timing claim, because nothing measures one.
export const PROVAS: [string, string][] = [
  ["2", "relatórios da B3 entram inteiros"],
  ["24", "tipos de movimentação reconhecidos"],
  ["3", "passos do extrato ao IRPF"],
];

export function ProofBand({ className = "" }: { className?: string }) {
  return (
    <dl
      className={`grid overflow-hidden rounded-lg border border-hairline bg-panel sm:grid-cols-3 ${className}`}
    >
      {PROVAS.map(([figure, label], i) => (
        <div
          key={label}
          className={`p-5 ${i > 0 ? "border-t border-hairline sm:border-l sm:border-t-0" : ""}`}
        >
          <dt className="font-display text-3xl font-extrabold tracking-tight text-ink tnum">
            {figure}
          </dt>
          <dd className="mt-1 text-sm leading-snug text-muted">{label}</dd>
        </div>
      ))}
    </dl>
  );
}
