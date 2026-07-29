// Chrome shared by the five landing variations, so each variant file holds only
// what makes it different: hero form, proof form, order of the argument.
// Same design system as the app (DESIGN.md): Bench Paper ground, Panel White
// surfaces, hairlines doing the separation, scarce navy, tnum on every figure.
import Link from "next/link";

import { Brand } from "@/components/Brand";
import { IconCheck } from "@/components/icons";
import { ALIQUOTA, DARF, GANHO, linePath } from "@/lib/apuracao";
import { formatMoney, formatPercent } from "@/lib/format";
import { PLANS } from "@/lib/plans";

// `inverted` flips ink and paper rather than reaching for navy: --navy is a light
// blue in dark mode, so white-on-navy would read at 2.4:1 there. Ink-on-paper
// swapped is the one inversion that stays legible in both themes.
export function Nav({ inverted = false }: { inverted?: boolean }) {
  return (
    <header
      className={`sticky top-0 z-20 border-b ${
        inverted ? "border-paper/20 bg-ink/95 text-paper" : "border-hairline bg-paper/85 text-ink"
      } backdrop-blur`}
    >
      <div className="mx-auto flex h-16 max-w-6xl items-center justify-between px-4 md:px-8">
        {/* The Q ring is navy inside Brand, which is invisible on the ink band —
            the arbitrary child selector recolours it without forking Brand. */}
        <Brand className={`text-lg ${inverted ? "text-paper [&_svg]:text-paper" : ""}`} />
        <nav className="flex items-center gap-4 sm:gap-6">
          <a
            href="#planos"
            className={`hidden py-2 text-sm font-medium transition-colors sm:block ${
              inverted ? "text-paper/70 hover:text-paper" : "text-muted hover:text-ink"
            }`}
          >
            Planos
          </a>
          <Link
            href="/login"
            className={`py-2 text-sm font-medium transition-colors ${
              inverted ? "text-paper/70 hover:text-paper" : "text-muted hover:text-ink"
            }`}
          >
            Entrar
          </Link>
          <Link
            href="/login"
            className={`rounded-md px-4 py-2.5 text-sm font-semibold transition-colors ${
              inverted ? "bg-paper text-ink hover:bg-paper/90" : "bg-navy text-white hover:bg-navy/90"
            }`}
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
  variant = "primary",
  className = "",
}: {
  children: React.ReactNode;
  href?: string;
  variant?: "primary" | "quiet" | "inverted";
  className?: string;
}) {
  const skin =
    variant === "primary"
      ? "bg-navy text-white hover:bg-navy/90 border-transparent"
      : variant === "inverted"
        ? "bg-paper text-ink hover:bg-paper/90 border-transparent"
        : "border-hairline bg-panel text-ink hover:bg-hairline/40";
  return (
    <Link
      href={href}
      className={`inline-block rounded-md border px-5 py-3 text-center text-sm font-semibold transition-colors ${skin} ${className}`}
    >
      {children}
    </Link>
  );
}

export const TRUST = "Sem cartão. Só leitura — nada executa na sua corretora.";

// The one figure the product exists to produce, as a readout. Used as the hero
// object in three variations, so it is a component and not three copies.
export function DarfCard({ className = "" }: { className?: string }) {
  return (
    <div className={`rounded-lg border border-hairline bg-panel p-5 ${className}`}>
      <div className="flex items-baseline justify-between gap-3">
        <p className="font-mono text-[11px] font-medium uppercase tracking-[0.14em] text-muted">
          DARF · código 6015
        </p>
        <span className="shrink-0 rounded-[4px] border border-hairline bg-paper px-1.5 py-0.5 font-mono text-[11px] font-medium uppercase leading-none tracking-wide text-muted">
          exemplo
        </span>
      </div>
      <p className="mt-2 font-display text-4xl font-extrabold tracking-tight text-ink tnum">
        {formatMoney(DARF)}
      </p>
      <p className="mt-1 text-sm text-muted">Julho de 2026 · vence 31/08</p>
    </div>
  );
}

// The app's own readout, trimmed to what fits beside a headline: three rows and
// the two totals. Every resultado is (último − médio) × qtde and both totals are
// the exact sums, so the card survives a calculator.
const POSICOES: [string, number, number, number][] = [
  ["PETR4", 400, 34.1, 38.42],
  ["VALE3", 100, 55.8, 61.1],
  ["HGLG11", 120, 158.4, 152.9],
];

export function CarteiraCard({ className = "" }: { className?: string }) {
  const rows = POSICOES.map(([ticker, qtd, medio, ultimo]) => ({
    ticker,
    valor: Math.round(ultimo * qtd * 100) / 100,
    resultado: Math.round((ultimo - medio) * qtd * 100) / 100,
  }));
  const totalValor = rows.reduce((s, r) => s + r.valor, 0);
  const totalResultado = rows.reduce((s, r) => s + r.resultado, 0);

  return (
    <div className={`overflow-hidden rounded-lg border border-hairline bg-panel ${className}`}>
      <div className="flex items-center justify-between gap-4 border-b border-hairline px-4 py-2.5">
        <p className="text-sm font-semibold text-ink">Carteira · posições</p>
        <span className="shrink-0 rounded-[4px] border border-hairline bg-paper px-1.5 py-0.5 font-mono text-[11px] font-medium uppercase leading-none tracking-wide text-muted">
          exemplo
        </span>
      </div>
      <table className="w-full border-collapse text-xs">
        <thead>
          <tr className="border-b border-hairline">
            {["Ativo", "Valor", "Resultado"].map((c, i) => (
              <th
                key={c}
                scope="col"
                className={`whitespace-nowrap px-4 py-2 text-[11px] font-semibold uppercase tracking-wide text-muted ${
                  i ? "text-right" : "text-left"
                }`}
              >
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.ticker} className="border-b border-hairline/70">
              <td className="px-4 py-2">
                <span className="rounded-[4px] border border-hairline bg-paper px-1.5 py-0.5 font-mono text-[11px] font-medium uppercase leading-tight text-ink">
                  {r.ticker}
                </span>
              </td>
              <td className="whitespace-nowrap px-4 py-2 text-right font-mono text-ink tnum">
                {formatMoney(r.valor)}
              </td>
              {/* Signed: a resultado is exactly the figure whose sign is the
                  information, so the sign leads and colour only reinforces. */}
              <td
                className={`whitespace-nowrap px-4 py-2 text-right font-mono tnum ${
                  r.resultado < 0 ? "text-loss" : "text-gain"
                }`}
              >
                {formatMoney(r.resultado, true)}
              </td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr className="bg-paper">
            <td className="px-4 py-2.5 font-medium text-ink">Total</td>
            <td className="whitespace-nowrap px-4 py-2.5 text-right font-mono font-medium text-ink tnum">
              {formatMoney(Math.round(totalValor * 100) / 100)}
            </td>
            <td className="whitespace-nowrap px-4 py-2.5 text-right font-mono font-medium text-gain tnum">
              {formatMoney(Math.round(totalResultado * 100) / 100, true)}
            </td>
          </tr>
        </tfoot>
      </table>
    </div>
  );
}

// Bruto vs depois do imposto, on one scale. The gap IS the alíquota.
const BRUTO = [100, 102, 101, 105, 108, 107, 112, 115, 118.2];
const LIQUIDO = BRUTO.map((v) => 100 + (v - 100) * (1 - ALIQUOTA));

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
      <path
        d={linePath(LIQUIDO, lo, hi)}
        fill="none"
        stroke="var(--series-1)"
        strokeWidth={1.8}
        strokeLinecap="round"
        strokeLinejoin="round"
        vectorEffect="non-scaling-stroke"
        pathLength={1}
        className="draw-line"
      />
    </svg>
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
    line: "O backtest desconta os 15% operação a operação.",
    Visual: () => <TaxedCurve className="h-full w-full" />,
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
      <div className="flex h-full flex-col justify-center rounded-[4px] bg-paper px-3">
        <p className="font-mono text-[11px] uppercase tracking-[0.14em] text-muted">DARF 6015</p>
        <p className="font-display text-2xl font-extrabold text-ink tnum">{formatMoney(DARF)}</p>
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
            <div className="flex items-baseline justify-between gap-2">
              <h3 className="text-sm font-semibold text-ink">{plan.name}</h3>
              {plan.featured && (
                <span className="shrink-0 font-mono text-[11px] font-medium uppercase tracking-[0.12em] text-navy">
                  Mais popular
                </span>
              )}
            </div>
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
// where the current page spends two paragraphs.
export const PROVAS: [string, string][] = [
  ["2", "relatórios da B3 entram inteiros"],
  ["24", "tipos de movimentação reconhecidos"],
  ["1 min", "do extrato à DARF"],
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

export function Bullet({ children }: { children: React.ReactNode }) {
  return (
    <li className="flex items-start gap-2.5 text-sm text-ink">
      <IconCheck size={18} className="mt-0.5 shrink-0 text-navy" />
      <span>{children}</span>
    </li>
  );
}
