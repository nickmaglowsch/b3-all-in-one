import Link from "next/link";
import { Brand } from "@/components/Brand";
import { Sparkline } from "@/components/charts/Sparkline";
import { CountUp, Reveal } from "@/components/landing/Motion";
import { PLANS } from "@/lib/plans";
import {
  IconCheck,
  IconClock,
  IconScissors,
  IconUpload,
  IconWallet,
} from "@/components/icons";

// Public marketing page. Renders standalone — AppShell bypasses chrome/guard for "/".
// All numbers here are illustrative product mockups, not live data.

// ── chart helper ──────────────────────────────────────────────────────────────
// values → SVG line + area paths over a 100×40 viewBox (stretched by the parent).
function areaLine(values: number[], lo?: number, hi?: number) {
  const min = lo ?? Math.min(...values);
  const max = hi ?? Math.max(...values);
  const rng = max - min || 1;
  const W = 100,
    H = 40,
    P = 4;
  const step = (W - P * 2) / (values.length - 1);
  const xy = values.map((v, i): [number, number] => [
    P + i * step,
    H - P - ((v - min) / rng) * (H - P * 2),
  ]);
  const line = xy
    .map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)} ${y.toFixed(1)}`)
    .join(" ");
  const area = `${line} L ${xy[xy.length - 1][0].toFixed(1)} ${H} L ${xy[0][0].toFixed(1)} ${H} Z`;
  return { line, area };
}

function AreaChart({
  values,
  id,
  color = "var(--series-1)",
}: {
  values: number[];
  id: string;
  color?: string;
}) {
  const { line, area } = areaLine(values);
  return (
    <svg
      viewBox="0 0 100 40"
      preserveAspectRatio="none"
      className="h-full w-full"
      aria-hidden
    >
      <defs>
        <linearGradient id={id} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor={color} stopOpacity="0.20" />
          <stop offset="1" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={area} fill={`url(#${id})`} />
      <path
        d={line}
        fill="none"
        stroke={color}
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

// ── ticker tape ───────────────────────────────────────────────────────────────
const QUOTES = [
  ["PETR4", "38,42", 1.2],
  ["VALE3", "61,10", -0.8],
  ["ITUB4", "33,05", 0.5],
  ["BBAS3", "27,80", 2.1],
  ["WEGE3", "52,30", -0.3],
  ["MGLU3", "12,15", 3.4],
  ["BBDC4", "15,90", -1.1],
  ["ABEV3", "13,45", 0.7],
  ["B3SA3", "12,88", 0.4],
  ["PRIO3", "44,20", 1.8],
  ["ITSA4", "10,32", 0.9],
  ["RENT3", "58,70", -0.6],
] as const;

function TickerTape() {
  const row = [...QUOTES, ...QUOTES]; // duplicated for a seamless -50% loop
  return (
    // Purely decorative, and the track is duplicated for the loop — without
    // this a screen reader announces 24 ticker/price/delta triplets twice.
    <div
      aria-hidden
      className="ticker overflow-hidden border-b border-hairline bg-panel/50"
    >
      <div className="marquee-track flex w-max items-center gap-8 py-2.5">
        {row.map(([sym, px, chg], i) => (
          <span key={`${sym}-${i}`} className="flex items-center gap-2 text-sm">
            <span className="font-mono font-medium text-ink">{sym}</span>
            <span className="tnum text-muted">{px}</span>
            <span
              className={`tnum text-xs font-medium ${chg >= 0 ? "text-gain" : "text-loss"}`}
            >
              {chg >= 0 ? "▲" : "▼"} {Math.abs(chg).toString().replace(".", ",")}%
            </span>
          </span>
        ))}
      </div>
    </div>
  );
}

// ── product peeks ─────────────────────────────────────────────────────────────
const POSITIONS = [
  { t: "PETR4", v: "R$ 18.240", p: "+2,1%", up: true, s: [3, 4, 3.6, 5, 4.8, 6, 6.4] },
  { t: "ITUB4", v: "R$ 12.980", p: "−0,8%", up: false, s: [6, 5.6, 6, 5, 4.7, 4.9, 4.4] },
  { t: "VALE3", v: "R$ 9.510", p: "+1,3%", up: true, s: [4, 3.8, 4.4, 4.2, 5, 4.9, 5.5] },
  { t: "HGLG11", v: "R$ 7.300", p: "+0,4%", up: true, s: [5, 5.1, 5, 5.2, 5.3, 5.2, 5.4] },
];

function DashboardPeek() {
  return (
    <div className="animate-in animate-float rounded-xl border border-hairline bg-panel p-5 shadow-xl shadow-navy/5">
      <div className="flex items-center justify-between">
        <div>
          <p className="font-mono text-[11px] font-medium uppercase tracking-[0.14em] text-muted">
            Patrimônio
          </p>
          <p className="mt-1 font-display text-3xl font-extrabold tracking-tight text-ink">
            <CountUp to={128430} prefix="R$ " />
          </p>
        </div>
        {/* Says what it is. These are mock figures (see note at top of file);
            claiming "ao vivo" over them was the page asserting data it has not
            got — the one thing this product's principles forbid. */}
        <span className="rounded-[4px] border border-hairline bg-paper px-1.5 py-0.5 font-mono text-[11px] font-medium uppercase leading-none tracking-wide text-muted">
          exemplo
        </span>
      </div>

      <div className="mt-1 flex items-center gap-2 text-sm">
        <span className="rounded-md bg-gain/10 px-1.5 py-0.5 font-medium text-gain tnum">
          +12,4%
        </span>
        <span className="text-muted tnum">+R$ 14.190 no ano</span>
      </div>

      <div className="mt-4 h-24">
        <AreaChart
          id="peek-hero"
          values={[100, 103, 101, 106, 110, 108, 113, 111, 117, 120, 124, 128]}
        />
      </div>

      <div className="mt-4 space-y-2.5 border-t border-hairline pt-4">
        {POSITIONS.slice(0, 3).map((r) => (
          <div key={r.t} className="flex items-center gap-3 text-sm">
            <span className="w-16 font-mono text-xs font-medium text-ink">
              {r.t}
            </span>
            <span aria-hidden>
              <Sparkline values={r.s} width={48} height={16} />
            </span>
            <span className="ml-auto tnum text-ink">{r.v}</span>
            <span className={`w-12 text-right tnum ${r.up ? "text-gain" : "text-loss"}`}>
              {r.p}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

function AllocPeek() {
  const bars = [
    { label: "Ações", pct: 55, color: "var(--series-1)" },
    { label: "FIIs", pct: 25, color: "var(--series-3)" },
    { label: "Renda fixa", pct: 15, color: "var(--series-4)" },
    { label: "Caixa", pct: 5, color: "var(--series-2)" },
  ];
  return (
    <div className="rounded-xl border border-hairline bg-panel p-5">
      <p className="text-sm font-semibold text-ink">Alocação-alvo</p>
      <div className="mt-4 space-y-3">
        {bars.map((b) => (
          <div key={b.label}>
            <div className="mb-1 flex justify-between text-xs">
              <span className="text-muted">{b.label}</span>
              <span className="tnum text-ink">{b.pct}%</span>
            </div>
            <div className="h-2 overflow-hidden rounded-full bg-hairline/60">
              <div
                className="h-full rounded-full"
                style={{ width: `${b.pct}%`, backgroundColor: b.color }}
              />
            </div>
          </div>
        ))}
      </div>
      <div className="mt-5 border-t border-hairline pt-4">
        <p className="mb-2 font-mono text-[11px] font-medium uppercase tracking-[0.14em] text-muted">
          Ordens sugeridas
        </p>
        <div className="space-y-2 text-sm">
          <div className="flex items-center justify-between">
            <span className="text-ink">
              <span className="text-gain">Comprar</span> HGLG11
            </span>
            <span className="tnum text-muted">+R$ 2.100</span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-ink">
              <span className="text-loss">Vender</span> PETR4
            </span>
            <span className="tnum text-muted">−R$ 1.400</span>
          </div>
        </div>
      </div>
    </div>
  );
}

function BenchPeek() {
  const cdi = [100, 101, 102, 103.5, 105, 106, 107.5, 109, 110.7];
  const port = [100, 102, 101, 105, 108, 107, 112, 115, 118.2];
  const all = [...cdi, ...port];
  const lo = Math.min(...all);
  const hi = Math.max(...all);
  const p = areaLine(port, lo, hi);
  const c = areaLine(cdi, lo, hi);
  return (
    <div className="rounded-xl border border-hairline bg-panel p-5">
      <div className="flex items-center justify-between">
        <p className="text-sm font-semibold text-ink">Carteira vs. CDI</p>
        <span className="rounded-md border border-hairline bg-paper px-1.5 py-0.5 text-xs font-medium text-muted">
          12 meses
        </span>
      </div>
      <div className="mt-4 h-32">
        <svg
          viewBox="0 0 100 40"
          preserveAspectRatio="none"
          className="h-full w-full"
          aria-hidden
        >
          <defs>
            <linearGradient id="benchgrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0" stopColor="var(--series-1)" stopOpacity="0.18" />
              <stop offset="1" stopColor="var(--series-1)" stopOpacity="0" />
            </linearGradient>
          </defs>
          <path d={p.area} fill="url(#benchgrad)" />
          <path
            d={c.line}
            fill="none"
            stroke="var(--muted)"
            strokeWidth={1.4}
            strokeDasharray="3 3"
            vectorEffect="non-scaling-stroke"
          />
          <path
            d={p.line}
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
      </div>
      <div className="mt-4 flex gap-6 border-t border-hairline pt-4 text-sm">
        <div className="flex items-center gap-2">
          <span className="h-2 w-4 rounded-full bg-series-1" />
          <span className="text-muted">Carteira</span>
          <span className="font-medium text-gain tnum">+18,2%</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="h-0.5 w-4 rounded-full bg-muted" />
          <span className="text-muted">CDI</span>
          <span className="text-muted tnum">+10,7%</span>
        </div>
      </div>
    </div>
  );
}

function IrpfPeek() {
  return (
    <div className="rounded-xl border border-hairline bg-panel p-5">
      <div className="flex items-center justify-between">
        <p className="text-sm font-semibold text-ink">IRPF 2026</p>
        <span className="inline-flex items-center gap-1 rounded-md bg-status-done/10 px-1.5 py-0.5 text-xs font-medium text-status-done">
          <IconCheck size={13} />
          Concluído
        </span>
      </div>

      <div className="mt-4 rounded-lg border border-navy/20 bg-navy/[0.04] p-4">
        <p className="font-mono text-[11px] font-medium uppercase tracking-[0.14em] text-muted">
          DARF · abril
        </p>
        <p className="mt-1 font-display text-2xl font-extrabold text-ink tnum">
          R$ 342,18
        </p>
        <p className="mt-1 text-xs text-muted">
          Código 6015 · vencimento 31/05
        </p>
      </div>

      <div className="mt-4 space-y-2.5 text-sm">
        <div className="flex justify-between">
          <span className="text-muted">Ganho apurado (ações)</span>
          <span className="tnum text-gain">+R$ 2.281,20</span>
        </div>
        <div className="flex justify-between">
          <span className="text-muted">Prejuízo a compensar</span>
          <span className="tnum text-loss">−R$ 640,00</span>
        </div>
        <div className="flex justify-between border-t border-hairline pt-2.5">
          <span className="text-ink">Alíquota</span>
          <span className="tnum text-ink">15%</span>
        </div>
      </div>
    </div>
  );
}

// ── static content ────────────────────────────────────────────────────────────
// Four, not six: IRPF and Bench used to sit here too, then got deep-dived in
// the showcase 60 lines below. Saying them twice made the grid filler and blew
// the ≤4-per-group budget. These four are the ones the showcase does not cover.
const FEATURES = [
  {
    Icon: IconWallet,
    title: "Carteira consolidada",
    body: "Posições, preço médio e proventos de todas as suas contas, direto dos extratos.",
  },
  {
    Icon: IconUpload,
    title: "Importação dos extratos",
    body: "Suba os extratos .xlsx da B3. A gente lê negociações e movimentações — nada à mão.",
  },
  {
    Icon: IconScissors,
    title: "Colheita de prejuízo",
    body: "O TLH aponta o que vender para compensar ganhos e pagar menos imposto.",
  },
  {
    Icon: IconClock,
    title: "Simulação",
    body: "Teste um aporte ou uma venda e veja o efeito na carteira e no imposto antes.",
  },
];

const SHOWCASE = [
  {
    eyebrow: "Estratégia",
    title: "Saiba exatamente o que comprar e vender",
    body: "Defina uma alocação-alvo e regras de rebalanceamento. O app calcula o desvio e sugere as ordens para voltar ao rumo — sem chute.",
    Peek: AllocPeek,
  },
  {
    eyebrow: "Performance",
    title: "Você está batendo o CDI?",
    body: "Compare sua carteira com benchmarks no período que quiser e teste estratégias em dados históricos antes de arriscar dinheiro de verdade.",
    Peek: BenchPeek,
  },
  {
    eyebrow: "Imposto",
    title: "O imposto de renda, resolvido",
    body: "DARF calculada mês a mês, prejuízos compensados automaticamente e a ficha de bens pronta para copiar na declaração.",
    Peek: IrpfPeek,
  },
];

const STEPS = [
  { n: "1", title: "Importe seus extratos", body: "Baixe os extratos .xlsx na Área do Investidor da B3 e arraste para dentro do app." },
  { n: "2", title: "Veja tudo consolidado", body: "Carteira, proventos e resultado, atualizados com o mercado." },
  { n: "3", title: "Declare e rebalanceie", body: "IRPF pronto, imposto otimizado e ordens para manter a alocação." },
];

export default function LandingPage() {
  return (
    <div className="min-h-screen bg-paper">
      <TickerTape />

      {/* Header */}
      <header className="sticky top-0 z-20 border-b border-hairline bg-paper/85 backdrop-blur">
        <div className="mx-auto flex h-16 max-w-6xl items-center justify-between px-4 md:px-8">
          <Brand className="text-lg" />
          {/* The persistent action is sign-up, not sign-in: nobody has an
              account yet. "Entrar" stays available as a quiet text link. */}
          <nav className="flex items-center gap-4 sm:gap-6">
            <a
              href="#recursos"
              className="hidden py-2 text-sm font-medium text-muted transition-colors hover:text-ink sm:block"
            >
              Recursos
            </a>
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

      <main>
      {/* Hero */}
      <section className="relative overflow-hidden">
        <div className="relative mx-auto grid max-w-6xl items-center gap-12 px-4 pt-16 pb-20 md:px-8 md:pt-24 lg:grid-cols-[1.1fr_0.9fr]">
          <div>
            <p className="animate-fade-up mb-4 font-mono text-[11px] font-medium uppercase tracking-[0.14em] text-muted">
              Para investidores da B3
            </p>
            <h1 className="animate-fade-up font-display text-4xl font-extrabold leading-[1.05] tracking-tight text-ink md:text-6xl">
              Do extrato da B3 ao imposto de renda, tudo em um só lugar.
            </h1>
            <p
              className="animate-fade-up mt-6 max-w-xl text-lg leading-relaxed text-muted"
              style={{ animationDelay: "80ms" }}
            >
              Importe seus extratos e tenha carteira consolidada, IRPF pronto,
              colheita de prejuízo e rebalanceamento — sem planilha e sem digitar
              nota por nota.
            </p>
            <div
              className="animate-fade-up mt-8 flex flex-wrap items-center gap-3"
              style={{ animationDelay: "160ms" }}
            >
              <Link
                href="/login"
                className="rounded-md bg-navy px-5 py-3 text-sm font-semibold text-white transition-colors hover:bg-navy/90"
              >
                Começar grátis
              </Link>
              <a
                href="#planos"
                className="rounded-md border border-hairline bg-panel px-5 py-3 text-sm font-semibold text-ink transition-colors hover:bg-hairline/40"
              >
                Ver planos
              </a>
            </div>
            <p
              className="animate-fade-up mt-4 text-sm text-muted"
              style={{ animationDelay: "240ms" }}
            >
              Sem cartão de crédito. Só leitura dos seus extratos — nada executa
              na sua corretora.
            </p>
          </div>

          <div
            className="animate-fade-up"
            style={{ animationDelay: "200ms" }}
          >
            <DashboardPeek />
          </div>
        </div>
      </section>

      {/* Stats band */}
      <section className="border-y border-hairline bg-panel/40">
        <div className="mx-auto grid max-w-6xl grid-cols-1 gap-8 px-4 py-10 text-center sm:grid-cols-3 sm:gap-4 md:px-8">
          {[
            // Every figure here is verifiable in the parser: 2 report kinds
            // (load_b3.py sniff_kind), 16 movement labels (MOV_CATEGORY), and
            // the 3 documented steps. Nothing here is a measured usage metric.
            { top: <CountUp to={2} />, label: "relatórios da B3: negociação e movimentação" },
            { top: <CountUp to={16} />, label: "tipos de movimentação reconhecidos" },
            { top: <CountUp to={3} />, label: "passos do extrato ao IRPF" },
          ].map((s, i) => (
            <div key={i}>
              <p className="font-display text-3xl font-extrabold text-navy md:text-4xl">
                {s.top}
              </p>
              <p className="mt-1 text-xs text-muted md:text-sm">{s.label}</p>
            </div>
          ))}
        </div>
      </section>

      {/* Features */}
      <section id="recursos" className="scroll-mt-24">
        <div className="mx-auto max-w-6xl px-4 py-16 md:px-8 md:py-24">
          <Reveal>
            <p className="mb-3 font-mono text-[11px] font-medium uppercase tracking-[0.14em] text-muted">
              Recursos
            </p>
            <h2 className="max-w-2xl font-display text-3xl font-extrabold tracking-tight text-ink md:text-4xl">
              Um app para todo o ciclo da sua carteira
            </h2>
          </Reveal>
          {/* No hover-lift: it was an undocumented third shadow and a transform
              the reduced-motion block never covered. The border carries it. */}
          <div className="mt-10 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {FEATURES.map(({ Icon, title, body }, i) => (
              <Reveal key={title} delay={(i % 4) * 80}>
                <div className="group h-full rounded-lg border border-hairline bg-panel p-5 transition-colors hover:border-navy/40">
                  <div className="flex h-10 w-10 items-center justify-center rounded-md bg-navy/[0.06] text-navy transition-colors group-hover:bg-navy group-hover:text-white">
                    <Icon size={20} />
                  </div>
                  <h3 className="mt-4 text-base font-semibold text-ink">{title}</h3>
                  <p className="mt-2 text-sm leading-relaxed text-muted">{body}</p>
                </div>
              </Reveal>
            ))}
          </div>
        </div>
      </section>

      {/* Feature showcases (alternating text / peek).
          The h2 below is the page's argument: without it these read as three
          separate tools, which is exactly what competitors already sell one of
          each. It also gives the section the heading its outline was missing. */}
      <section className="border-t border-hairline bg-panel/40">
        <div className="mx-auto max-w-6xl px-4 py-20 md:px-8 md:py-28">
          <Reveal>
            <p className="mb-3 font-mono text-[11px] font-medium uppercase tracking-[0.14em] text-muted">
              Por que tudo junto
            </p>
            <h2 className="max-w-3xl font-display text-3xl font-extrabold tracking-tight text-ink md:text-4xl">
              O mesmo motor de imposto que pontua o backtest calcula sua DARF.
            </h2>
            <p className="mt-4 max-w-xl leading-relaxed text-muted">
              Estratégia, acompanhamento e imposto não são três ferramentas
              soltas aqui — são o mesmo cálculo, do teste à declaração.
            </p>
          </Reveal>

          <div className="mt-16 space-y-20">
          {SHOWCASE.map(({ eyebrow, title, body, Peek }, i) => (
            <Reveal key={title}>
              <div
                className={`grid items-center gap-10 lg:grid-cols-2 ${
                  i % 2 ? "lg:[&>*:first-child]:order-2" : ""
                }`}
              >
                <div>
                  <p className="mb-3 font-mono text-[11px] font-medium uppercase tracking-[0.14em] text-muted">
                    {eyebrow}
                  </p>
                  <h3 className="font-display text-2xl font-extrabold tracking-tight text-ink md:text-3xl">
                    {title}
                  </h3>
                  <p className="mt-4 max-w-md leading-relaxed text-muted">{body}</p>
                </div>
                <Peek />
              </div>
            </Reveal>
          ))}
          </div>
        </div>
      </section>

      {/* How it works */}
      <section className="mx-auto max-w-6xl px-4 py-16 md:px-8 md:py-24">
        <Reveal>
          <p className="mb-3 font-mono text-[11px] font-medium uppercase tracking-[0.14em] text-muted">
            Como funciona
          </p>
          <h2 className="max-w-2xl font-display text-3xl font-extrabold tracking-tight text-ink md:text-4xl">
            Do extrato ao imposto em três passos
          </h2>
        </Reveal>
        <div className="mt-10 grid gap-6 md:grid-cols-3">
          {STEPS.map(({ n, title, body }, i) => (
            <Reveal key={n} delay={i * 100}>
              <div>
                <span className="font-display text-3xl font-extrabold text-navy tnum">
                  {n}
                </span>
                <h3 className="mt-3 text-base font-semibold text-ink">{title}</h3>
                <p className="mt-2 text-sm leading-relaxed text-muted">{body}</p>
              </div>
            </Reveal>
          ))}
        </div>
      </section>

      {/* Pricing */}
      <section id="planos" className="scroll-mt-24 border-t border-hairline bg-panel/40">
        <div className="mx-auto max-w-6xl px-4 py-16 md:px-8 md:py-24">
          <Reveal>
            <p className="mb-3 font-mono text-[11px] font-medium uppercase tracking-[0.14em] text-muted">
              Planos
            </p>
            <h2 className="max-w-2xl font-display text-3xl font-extrabold tracking-tight text-ink md:text-4xl">
              Preço simples, em reais
            </h2>
            <p className="mt-3 max-w-2xl text-muted">
              Comece de graça e assine quando precisar de imposto e estratégia.
              Cancele quando quiser.
            </p>
          </Reveal>

          <div className="mt-10 grid gap-5 md:grid-cols-2 xl:grid-cols-4">
            {PLANS.map((plan, i) => (
              <Reveal key={plan.name} delay={i * 90} className="h-full">
                <div
                  className={`relative flex h-full flex-col rounded-lg border bg-panel p-6 transition-colors ${
                    plan.featured
                      ? "border-navy ring-1 ring-navy"
                      : "border-hairline"
                  }`}
                >
                  {plan.featured && (
                    <span className="absolute -top-3 left-6 rounded-full bg-navy px-3 py-1 font-mono text-[11px] font-medium uppercase tracking-[0.12em] text-white">
                      Mais popular
                    </span>
                  )}
                  <h3 className="text-sm font-semibold text-ink">{plan.name}</h3>
                  <p className="mt-1 text-sm text-muted">{plan.tagline}</p>
                  <p className="mt-5 flex items-baseline gap-1">
                    <span className="font-display text-4xl font-extrabold tracking-tight text-ink tnum">
                      {plan.price}
                    </span>
                    <span className="text-sm text-muted">{plan.cadence}</span>
                  </p>

                  <ul className="mt-6 flex-1 space-y-3">
                    {plan.features.map((f) => (
                      <li key={f} className="flex items-start gap-2.5 text-sm text-ink">
                        <IconCheck size={18} className="mt-0.5 shrink-0 text-navy" />
                        <span>{f}</span>
                      </li>
                    ))}
                  </ul>

                  <Link
                    href={plan.id === "gratis" ? "/login" : "/assinatura"}
                    className={`mt-8 rounded-md px-4 py-2.5 text-center text-sm font-semibold transition-colors ${
                      plan.featured
                        ? "bg-navy text-white hover:bg-navy/90"
                        : "border border-hairline text-ink hover:bg-hairline/40"
                    }`}
                  >
                    {plan.cta}
                  </Link>
                </div>
              </Reveal>
            ))}
          </div>
        </div>
      </section>

      {/* Closing CTA */}
      <section className="mx-auto max-w-6xl px-4 py-20 text-center md:px-8 md:py-28">
        <Reveal>
          <h2 className="mx-auto max-w-2xl font-display text-3xl font-extrabold tracking-tight text-ink md:text-4xl">
            Sua carteira da B3, sem planilha.
          </h2>
          <p className="mx-auto mt-4 max-w-xl text-muted">
            Leva um minuto para importar o primeiro extrato.
          </p>
          <Link
            href="/login"
            className="mt-8 inline-block rounded-md bg-navy px-6 py-3 text-sm font-semibold text-white transition-colors hover:bg-navy/90"
          >
            Começar grátis
          </Link>
        </Reveal>
      </section>
      </main>

      {/* Footer */}
      <footer className="border-t border-hairline">
        <div className="mx-auto flex max-w-6xl flex-col gap-4 px-4 py-8 md:flex-row md:items-center md:justify-between md:px-8">
          <Brand className="text-base" />
          <p className="max-w-md text-xs leading-relaxed text-muted">
            Ferramenta de organização de carteira e apuração. Não é recomendação
            de investimento nem serviço de contabilidade.
          </p>
        </div>
      </footer>
    </div>
  );
}
