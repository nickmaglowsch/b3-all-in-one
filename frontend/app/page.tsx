import Link from "next/link";
import { Brand } from "@/components/Brand";
import { Reveal } from "@/components/landing/Motion";
import { formatMoney, formatPercent } from "@/lib/format";
import { AVULSOS, BASE as CARTEIRA, BUNDLE, TUDO } from "@/lib/modules";

// ── Direction contract ────────────────────────────────────────────────────────
// THESIS: one tax engine, shown in cross-section. Refuses the SaaS stack this
//   page used to be — hero + icon-card grid + alternating showcase + pricing —
//   which claimed the loop in an h2 and then demonstrated three separate tools.
// OWN-WORLD: unchanged Quantax Instrument Panel (DESIGN.md). Bench
//   Paper ground, Panel White surfaces, Etched Rule hairlines doing all
//   separation, Archivo 800 / Plex Sans 14 / Plex Mono tnum, scarce navy.
// STORY: a B3 investor arrives believing backtest, carteira and IRPF are three
//   products. They read down one machine and watch a single apurado figure
//   surface identically in all three outlets. The loop is one calculation.
// FIRST VIEWPORT: thesis headline left at display scale over open paper, both
//   actions under it; the B3 extract — the engine's real intake — spanning the
//   full column beneath and bleeding off the right edge as a continuing sheet.
// FORM: "the engine in cross-section", candidate 6 of the grounded structural
//   list, staged on a vertical signal path. Seed key b8b693da, scope surface.
//   The dealt staging (first-viewport-live-mirror) was weighed and dropped: it
//   foregrounds deadline anxiety over the mechanism, losing on product clarity.
//
// Every figure below is either traceable to the parser (2 report kinds, 16
// MOV_CATEGORY labels, the real Negociação column names) or synthetic demo data
// labelled "exemplo". The apuração chain is arithmetically closed on purpose —
// it is the page's argument, so it has to survive being checked.

// ── the engine's signal path ──────────────────────────────────────────────────
// Stages clamp onto one continuous rule: a fixed-width gutter whose right
// border is contiguous across stacked stages, so the spine needs no absolute
// positioning. The marker names a position in the path, and the order is the
// information — extract before apuração before outlet.
function Stage({
  id,
  stage,
  marker,
  still,
  children,
}: {
  id?: string;
  // Rides the spine from md up, so the path reads as a labelled scale instead
  // of an empty gutter with a rule on it. Below md the gutter is 1.5rem and the
  // name folds back into the eyebrow, where it used to live.
  stage: string;
  marker: string;
  // The first stage is already on screen at load: it fills its node without
  // playing the entrance the scrolled-to stages get.
  still?: boolean;
  children: React.ReactNode;
}) {
  return (
    <Reveal still={still}>
      <section id={id} className="scroll-mt-20 grid grid-cols-[1.5rem_1fr] md:grid-cols-[7rem_1fr]">
        <div className="relative border-r border-hairline md:pt-24">
          {/* Sits on the rule, centred on the stage label's line — the signal
              arriving at this stage. */}
          <span
            aria-hidden
            className="stage-node absolute -right-[4.5px] top-[4.2rem] h-2 w-2 rounded-full border border-hairline bg-paper md:top-[6.2rem]"
          />
          {/* Ink, not Legend Grey: this is the instrument's own scale rather
              than an eyebrow over a title, and contrast is the lever the design
              system points at before the accent. */}
          <p className="hidden pr-6 text-right font-mono text-[11px] font-medium uppercase tracking-[0.14em] text-ink md:block">
            {stage}
          </p>
        </div>
        <div className="min-w-0 py-16 pl-4 pr-4 md:py-24 md:pl-12 md:pr-8">
          <p className="mb-4 font-mono text-[11px] font-medium uppercase tracking-[0.14em] text-muted">
            <span className="md:hidden">{stage} · </span>
            {marker}
          </p>
          {children}
        </div>
      </section>
    </Reveal>
  );
}

// ── stage 1 · the intake ──────────────────────────────────────────────────────
// Real Negociação column names, verbatim from taxcore/scripts/load_b3.py. The
// four vendas sum to exactly the R$ 24.310,00 the apuração opens on below.
const EXTRACT_COLS = [
  "Data do Negócio",
  "Tipo de Movimentação",
  "Mercado",
  "Prazo/Vencimento",
  "Instituição",
  "Código de Negociação",
  "Quantidade",
  "Preço",
  "Valor",
];

const EXTRACT_ROWS: [string, string, string, string, string, string, number, number, number][] = [
  ["15/07/2026", "Venda", "Mercado à Vista", "—", "XP INVESTIMENTOS CCTVM S.A.", "PETR4", 400, 38.42, 15368],
  ["15/07/2026", "Venda", "Mercado à Vista", "—", "XP INVESTIMENTOS CCTVM S.A.", "VALE3", 100, 61.1, 6110],
  ["14/07/2026", "Compra", "Mercado à Vista", "—", "CLEAR CORRETORA LTDA", "ITUB4", 300, 33.05, 9915],
  ["10/07/2026", "Venda", "Mercado Fracionário", "—", "XP INVESTIMENTOS CCTVM S.A.", "BBAS3", 60, 27.8, 1668],
  ["03/07/2026", "Venda", "Mercado à Vista", "—", "CLEAR CORRETORA LTDA", "WEGE3", 20, 58.2, 1164],
];

// A sample of MOV_CATEGORY (taxcore/scripts/load_b3.py), not the whole list —
// the count above it is the claim, and that has to match the parser. Every
// figure on this page is traceable there; keep them in step.
const MOV_LABELS = [
  "Dividendo",
  "Juros Sobre Capital Próprio",
  "Rendimento",
  "Amortização",
  "Desdobro",
  "Bonificação em Ativos",
  "Fração em Ativos",
  "Transferência - Liquidação",
  "APLICAÇÃO",
  "VENCIMENTO",
  "RESGATE ANTECIPADO",
  "PAGAMENTO DE JUROS",
];

function ExtractSheet() {
  return (
    <div className="overflow-hidden rounded-lg border border-hairline bg-panel">
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-b border-hairline px-4 py-2.5">
        <div className="flex items-end gap-px">
          <span className="rounded-t-[4px] border border-b-0 border-hairline bg-paper px-3 py-1.5 font-mono text-[11px] font-medium text-ink">
            Negociação
          </span>
          <span className="rounded-t-[4px] border border-b-0 border-transparent px-3 py-1.5 font-mono text-[11px] font-medium text-muted">
            Movimentação
          </span>
        </div>
        {/* ml-auto so the badge stays on the right even on the narrow screens
            where it drops to its own line. */}
        <span className="ml-auto shrink-0 rounded-[4px] border border-hairline bg-paper px-1.5 py-0.5 font-mono text-[11px] font-medium uppercase leading-none tracking-wide text-muted">
          exemplo
        </span>
      </div>

      {/* Bleeds right: a spreadsheet does not stop at the content column. */}
      <div className="overflow-x-auto">
        <table className="w-max min-w-full border-collapse text-sm">
          <thead>
            <tr className="border-b border-hairline">
              {EXTRACT_COLS.map((c) => (
                <th
                  key={c}
                  scope="col"
                  className="whitespace-nowrap px-3 py-2 text-left text-[11px] font-semibold uppercase tracking-wide text-muted"
                >
                  {c}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {EXTRACT_ROWS.map(([data, tipo, mercado, prazo, inst, tick, qtd, preco, valor], i) => (
              <tr key={i} className="border-b border-hairline/70 last:border-0">
                <td className="whitespace-nowrap px-3 py-2 font-mono text-xs text-ink tnum">{data}</td>
                <td className="whitespace-nowrap px-3 py-2 text-ink">{tipo}</td>
                <td className="whitespace-nowrap px-3 py-2 text-muted">{mercado}</td>
                <td className="whitespace-nowrap px-3 py-2 text-center text-muted">{prazo}</td>
                <td className="whitespace-nowrap px-3 py-2 text-muted">{inst}</td>
                <td className="whitespace-nowrap px-3 py-2">
                  <span className="rounded-[4px] border border-hairline bg-paper px-1.5 py-0.5 font-mono text-[11px] font-medium uppercase leading-tight text-ink">
                    {tick}
                  </span>
                </td>
                <td className="whitespace-nowrap px-3 py-2 text-right font-mono text-ink tnum">{qtd}</td>
                <td className="whitespace-nowrap px-3 py-2 text-right font-mono text-ink tnum">
                  {preco.toLocaleString("pt-BR", { minimumFractionDigits: 2 })}
                </td>
                <td className="whitespace-nowrap px-3 py-2 text-right font-mono text-ink tnum">
                  {valor.toLocaleString("pt-BR", { minimumFractionDigits: 2 })}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ── stage 1 · what the intake becomes ─────────────────────────────────────────
// The app's posições table, laid over the extract it was computed from: same
// tickers, and the last price of each is the price on the sheet underneath.
// Both totals are the exact sums of their columns and every resultado is
// (último − preço médio) × quantidade, so the panel survives a calculator the
// same way the apuração does. Labelled exemplo, like every synthetic figure
// here — the product is pre-launch and has no book of its own to show.
const POSICOES: [string, number, number, number][] = [
  ["PETR4", 400, 34.1, 38.42],
  ["VALE3", 100, 55.8, 61.1],
  ["ITUB4", 300, 33.05, 34.2],
  ["HGLG11", 120, 158.4, 152.9],
  ["WEGE3", 200, 52.1, 58.2],
];

const cents = (n: number) => Math.round(n * 100) / 100;
const preco = (n: number) => n.toLocaleString("pt-BR", { minimumFractionDigits: 2 });

function PositionsPanel() {
  const rows = POSICOES.map(([ticker, qtd, medio, ultimo]) => ({
    ticker,
    qtd,
    medio,
    valor: cents(ultimo * qtd),
    resultado: cents((ultimo - medio) * qtd),
  }));
  const totalValor = cents(rows.reduce((s, r) => s + r.valor, 0));
  const totalResultado = cents(rows.reduce((s, r) => s + r.resultado, 0));

  return (
    // The one overlapping surface in the product, so the one shadow: offset and
    // soft blur, saying which sheet is on top. Only from xl, where the overlap
    // exists — below that the panel is stacked in flow and a shadow on a flat
    // surface floating over nothing is exactly what this system refuses. Dark
    // mode needs less of it either way: Panel White already sits a tonal step
    // above the page there.
    <div className="overflow-hidden rounded-lg border border-hairline bg-panel xl:shadow-[0_18px_45px_-18px_rgb(23_24_28_/_0.35)]">
      <div className="flex items-center justify-between gap-4 border-b border-hairline px-4 py-2.5">
        <p className="text-sm font-semibold text-ink">Carteira · posições</p>
        <span className="shrink-0 rounded-[4px] border border-hairline bg-paper px-1.5 py-0.5 font-mono text-[11px] font-medium uppercase leading-none tracking-wide text-muted">
          exemplo
        </span>
      </div>
      <table className="w-full border-collapse text-xs">
        <thead>
          <tr className="border-b border-hairline">
            {/* Qtde and preço médio are the inputs; ativo, valor and resultado
                are what you came to read. Five columns need 470px and a phone
                gives the panel 287, so the inputs go rather than the table
                growing a scrollbar the readout does not need. */}
            {["Ativo", "Qtde", "Preço médio", "Valor", "Resultado"].map((c, i) => (
              <th
                key={c}
                scope="col"
                className={`whitespace-nowrap px-2.5 py-2 text-[11px] font-semibold uppercase tracking-wide text-muted ${
                  i ? "text-right" : "text-left"
                } ${i === 1 || i === 2 ? "hidden sm:table-cell" : ""}`}
              >
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.ticker} className="border-b border-hairline/70">
              <td className="px-2.5 py-2">
                <span className="rounded-[4px] border border-hairline bg-paper px-1.5 py-0.5 font-mono text-[11px] font-medium uppercase leading-tight text-ink">
                  {r.ticker}
                </span>
              </td>
              <td className="hidden whitespace-nowrap px-2.5 py-2 text-right font-mono text-ink tnum sm:table-cell">
                {r.qtd}
              </td>
              <td className="hidden whitespace-nowrap px-2.5 py-2 text-right font-mono text-ink tnum sm:table-cell">
                {preco(r.medio)}
              </td>
              <td className="whitespace-nowrap px-2.5 py-2 text-right font-mono text-ink tnum">
                {preco(r.valor)}
              </td>
              {/* Signed, so the sign leads and the colour only reinforces it —
                  and a resultado is exactly the kind of number whose sign is
                  the information. */}
              <td
                className={`whitespace-nowrap px-2.5 py-2 text-right font-mono tnum ${
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
            {/* Empty cells rather than a colSpan: a colSpan reaching across the
                two columns that vanish below sm makes the browser keep them
                alive, and the table blows past the panel by 190px. */}
            <td className="px-2.5 py-2.5 text-left font-medium text-ink">Total</td>
            <td className="hidden sm:table-cell" />
            <td className="hidden sm:table-cell" />
            <td className="whitespace-nowrap px-2.5 py-2.5 text-right font-mono font-medium text-ink tnum">
              {formatMoney(totalValor)}
            </td>
            <td className="whitespace-nowrap px-2.5 py-2.5 text-right font-mono font-medium text-gain tnum">
              {formatMoney(totalResultado, true)}
            </td>
          </tr>
        </tfoot>
      </table>
    </div>
  );
}

// ── stage 2 · the core ────────────────────────────────────────────────────────
// The chain is closed: 24310 − 22028.80 = 2281.20 − 640 = 1641.20 × 15% = 246.18.
const VENDAS = 24310;
const CUSTO = 22028.8;
const GANHO = VENDAS - CUSTO;
const PREJUIZO = 640;
const BASE = GANHO - PREJUIZO;
const ALIQUOTA = 0.15;
const DARF = Math.round(BASE * ALIQUOTA * 100) / 100;

const APURACAO: {
  label: string;
  note?: string;
  value: string;
  strong?: boolean;
  // The figure the rest of the page tracks is born here. Marking it at its
  // origin is what teaches the mark: by the time it reappears in the three
  // outlets the reader already knows what the tint means.
  mark?: boolean;
}[] = [
  { label: "Vendas de ações no mês", note: "4 operações, dos extratos", value: formatMoney(VENDAS) },
  { label: "Isenção mensal de R$ 20.000", note: "excedida — o mês é tributável", value: "não se aplica" },
  { label: "Custo pelo preço médio", note: "ajustado por desdobros e amortizações", value: `− ${formatMoney(CUSTO)}` },
  { label: "Ganho apurado", value: formatMoney(GANHO, true), strong: true, mark: true },
  { label: "Prejuízo a compensar", note: "saldo acumulado da mesma classe", value: `− ${formatMoney(PREJUIZO)}` },
  { label: "Base de cálculo", value: formatMoney(BASE) },
  { label: "Alíquota", note: "ações, operação comum", value: formatPercent(ALIQUOTA) },
];

// ── stage 3 · the three outlets ───────────────────────────────────────────────
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
  return xy
    .map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)} ${y.toFixed(1)}`)
    .join(" ");
}

// The shared figure, marked wherever it surfaces. Navy tint at 6% is the same
// "you are here" treatment the active nav item uses — the accent marking a
// location, not colouring data.
// `tone="inherit"` is for the one place the marked figure already carries its
// own colour — the apurado gain, which is green because its sign is the
// information. The mark says "this is the tracked number"; it does not get to
// overrule what the number means.
function Same({
  children,
  tone = "ink",
}: {
  children: React.ReactNode;
  tone?: "ink" | "inherit";
}) {
  return (
    // Padding in em and no weight of its own, so the mark holds its proportions
    // on a 14px prose figure and on the 36px one in the section head, and never
    // lightens the display type it sits inside.
    <mark
      className={`rounded-[4px] bg-navy/[0.08] px-[0.25em] ${
        tone === "ink" ? "text-ink" : "text-inherit"
      }`}
    >
      {children}
    </mark>
  );
}

// Base 100, so the last point is the return. Read off the series rather than
// written beside it: a hand-typed "+18,2%" is exactly the kind of figure this
// page argues it never produces.
const asReturn = (series: number[]) =>
  `+${formatPercent(Math.round((series[series.length - 1] / 100 - 1) * 1000) / 1000)}`;

function OutletBench() {
  const bruto = [100, 102, 101, 105, 108, 107, 112, 115, 118.2];
  const liquido = bruto.map((v) => 100 + (v - 100) * (1 - ALIQUOTA));
  const all = [...bruto, ...liquido];
  const lo = Math.min(...all);
  const hi = Math.max(...all);
  return (
    <>
      <div className="mt-5 flex h-36 items-center">
        <svg viewBox="0 0 100 40" preserveAspectRatio="none" className="h-28 w-full" aria-hidden>
          <path
            d={areaLine(bruto, lo, hi)}
            fill="none"
            stroke="var(--axis)"
            strokeWidth={1.4}
            strokeDasharray="3 3"
            vectorEffect="non-scaling-stroke"
          />
          <path
            d={areaLine(liquido, lo, hi)}
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
      <dl className="mt-5 space-y-2 border-t border-hairline pt-4 text-sm">
        <div className="flex items-baseline justify-between gap-3">
          <dt className="flex items-center gap-2 text-muted">
            {/* Dashed, because that is how the chart draws it. A legend key
                that does not match its line is a broken instrument. */}
            <span
              aria-hidden
              className="h-0.5 w-4 bg-[repeating-linear-gradient(90deg,var(--axis)_0_3px,transparent_3px_6px)]"
            />
            Retorno bruto
          </dt>
          <dd className="tnum text-muted">{asReturn(bruto)}</dd>
        </div>
        <div className="flex items-baseline justify-between gap-3">
          <dt className="flex items-center gap-2 text-muted">
            <span aria-hidden className="h-0.5 w-4 rounded-full bg-series-1" />
            Depois do imposto
          </dt>
          <dd className="tnum font-medium text-ink">{asReturn(liquido)}</dd>
        </div>
      </dl>
      <p className="mt-auto pt-4 text-sm leading-relaxed text-muted">
        A distância entre as curvas é a <Same>mesma alíquota de 15%</Same>,
        descontada operação a operação.
      </p>
    </>
  );
}

function OutletCarteira() {
  const ordens = [
    { acao: "Vender", ticker: "PETR4", valor: -1400, motivo: "acima do alvo" },
    { acao: "Comprar", ticker: "HGLG11", valor: 2100, motivo: "abaixo do alvo" },
  ];
  return (
    <>
      <dl className="mt-5 flex h-36 flex-col justify-center space-y-3">
        {[
          { label: "Ações", pct: 0.55, color: "var(--series-1)" },
          { label: "FIIs", pct: 0.25, color: "var(--series-3)" },
          { label: "Renda fixa", pct: 0.15, color: "var(--series-4)" },
          { label: "Caixa", pct: 0.05, color: "var(--series-2)" },
        ].map((b) => (
          <div key={b.label}>
            <div className="mb-1 flex justify-between text-xs">
              <dt className="text-muted">{b.label}</dt>
              <dd className="tnum text-ink">{formatPercent(b.pct)}</dd>
            </div>
            <div aria-hidden className="h-1.5 overflow-hidden rounded-full bg-hairline/60">
              <div className="h-full rounded-full" style={{ width: `${b.pct * 100}%`, backgroundColor: b.color }} />
            </div>
          </div>
        ))}
      </dl>
      <ul className="mt-5 space-y-2 border-t border-hairline pt-4 text-sm">
        {ordens.map((o) => (
          <li key={o.ticker} className="flex items-baseline justify-between gap-3">
            <span className="text-ink">
              {/* Not green/red: an order's direction is not a P&L sign, and in
                  this system green means gain. The verb carries the meaning. */}
              <span className="font-medium">{o.acao}</span>{" "}
              <span className="font-mono text-xs uppercase">{o.ticker}</span>{" "}
              <span className="text-muted">· {o.motivo}</span>
            </span>
            <span className="shrink-0 tnum text-muted">{formatMoney(o.valor, true)}</span>
          </li>
        ))}
      </ul>
      <p className="mt-auto pt-4 text-sm leading-relaxed text-muted">
        O plano sabe que essa venda realiza <Same>{formatMoney(GANHO)}</Same> antes
        de sugerir a ordem.
      </p>
    </>
  );
}

function OutletIrpf() {
  return (
    <>
      {/* A tonal band, not a card inside a card — and at 2xl rather than 3xl,
          because the figure this section is about is the marked one below it.
          The DARF already had its display moment at the end of the apuração. */}
      <div className="mt-5 flex h-36 flex-col justify-center rounded-lg bg-paper p-4">
        <p className="font-mono text-[11px] font-medium uppercase tracking-[0.14em] text-muted">
          DARF · código 6015
        </p>
        <p className="mt-1 font-display text-2xl font-extrabold text-ink tnum">
          {formatMoney(DARF)}
        </p>
        <p className="mt-1 text-xs text-muted">Vencimento 31/08/2026</p>
      </div>
      <dl className="mt-5 space-y-2 border-t border-hairline pt-4 text-sm">
        <div className="flex items-baseline justify-between gap-3">
          <dt className="text-muted">Renda variável · julho</dt>
          <dd className="tnum text-ink">
            <Same>{formatMoney(GANHO, true)}</Same>
          </dd>
        </div>
        <div className="flex items-baseline justify-between gap-3">
          <dt className="text-muted">Bens e Direitos</dt>
          <dd className="tnum text-ink">12 linhas</dd>
        </div>
      </dl>
      <p className="mt-auto pt-4 text-sm leading-relaxed text-muted">
        O mesmo ganho entra na ficha de renda variável, pronto para copiar na
        declaração.
      </p>
    </>
  );
}

const OUTLETS = [
  {
    tag: "Bench",
    title: "O backtest desconta o imposto",
    Body: OutletBench,
  },
  {
    tag: "Carteira",
    title: "O rebalanceamento conta o imposto",
    Body: OutletCarteira,
  },
  {
    tag: "IRPF",
    title: "A declaração recebe o imposto",
    Body: OutletIrpf,
  },
];

// ── stage 4 · the fiscal year ─────────────────────────────────────────────────
const MESES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"];

export default function LandingPage() {
  return (
    // overflow-x-clip (not hidden — hidden would kill the sticky header) so the
    // intake sheet's bleed can overshoot by the scrollbar's width without ever
    // producing a horizontal scrollbar on the page itself.
    <div className="min-h-screen overflow-x-clip bg-paper">
      <header className="sticky top-0 z-20 border-b border-hairline bg-paper/85 backdrop-blur">
        <div className="mx-auto flex h-16 max-w-6xl items-center justify-between px-4 md:px-8">
          <Brand className="text-lg" />
          {/* The persistent action is sign-up, not sign-in: nobody has an
              account yet. "Entrar" stays available as a quiet text link. */}
          <nav className="flex items-center gap-4 sm:gap-6">
            <a
              href="#motor"
              className="hidden py-2 text-sm font-medium text-muted transition-colors hover:text-ink sm:block"
            >
              Como funciona
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

      <main className="mx-auto max-w-6xl px-4 md:px-8">
        {/* ── Stage 1 · intake ─────────────────────────────────────────────── */}
        <Stage stage="Entrada" marker="o extrato da B3" still>
          {/* Two sentences, so two blocks. Left to wrap they broke as "Um motor
              de imposto. Três / saídas." at every width — the thesis split
              across the one line break the reader is guaranteed to notice. */}
          <h1 className="max-w-4xl font-display text-[clamp(2.5rem,6.5vw,5rem)] font-extrabold leading-[1.05] tracking-tight text-ink">
            <span className="block">Um motor de imposto.</span>
            <span className="block">Três saídas.</span>
          </h1>
          {/* Names the three saídas the headline promises, in the order the
              page shows them. The old second sentence ("daí em diante é um
              número só") only restated the h1 in prettier words. */}
          <p className="mt-6 max-w-[65ch] text-lg leading-relaxed text-muted">
            Você sobe os extratos da B3. O mesmo cálculo apura a carteira,
            desconta o imposto no backtest e emite a DARF.
          </p>
          <div className="mt-8 flex flex-wrap items-center gap-3">
            <Link
              href="/login"
              className="rounded-md bg-navy px-5 py-3 text-sm font-semibold text-white transition-colors hover:bg-navy/90"
            >
              Começar grátis
            </Link>
            <a
              href="#motor"
              className="rounded-md border border-hairline bg-panel px-5 py-3 text-sm font-semibold text-ink transition-colors hover:bg-hairline/40"
            >
              Ver o cálculo
            </a>
          </div>
          <p className="mt-4 text-sm text-muted">
            Sem cartão de crédito. Só leitura — nada executa na sua corretora.
          </p>

          {/* The intake running off the right edge of the screen, and the book
              it becomes laid over it — the page's whole claim in one frame,
              before a word of the argument. The wrapper is the normal content
              column, so the panel's right edge lands on the page grid while the
              sheet keeps going past it; below xl the two simply stack. */}
          {/* mb-44 is the panel's overhang past the sheet plus the gap the next
              block needs — an absolute child contributes no height of its own. */}
          <div className="relative mt-14 xl:mb-44">
            <div className="bleed-right">
              <ExtractSheet />
            </div>
            <div className="mt-6 xl:absolute xl:right-0 xl:top-32 xl:mt-0 xl:w-[33rem]">
              <PositionsPanel />
            </div>
          </div>

          <div className="mt-6 grid gap-6 md:grid-cols-[auto_1fr] md:gap-10">
            <p className="max-w-xs text-sm leading-relaxed text-muted">
              <span className="text-ink">Dois relatórios</span> da Área do
              Investidor entram inteiros: negociação e movimentação.
            </p>
            <div>
              <p className="mb-2 font-mono text-[11px] font-medium uppercase tracking-[0.14em] text-muted">
                24 tipos de movimentação reconhecidos
              </p>
              <ul className="flex flex-wrap gap-1.5">
                {MOV_LABELS.map((m) => (
                  <li
                    key={m}
                    className="rounded-[4px] border border-hairline bg-panel px-1.5 py-0.5 font-mono text-[11px] leading-tight text-muted"
                  >
                    {m}
                  </li>
                ))}
                <li className="px-1.5 py-0.5 font-mono text-[11px] leading-tight text-muted">
                  e mais 8
                </li>
              </ul>
            </div>
          </div>
        </Stage>

        {/* ── Stage 2 · the core ───────────────────────────────────────────── */}
        <Stage id="motor" stage="Apuração" marker="o núcleo">
          <h2 className="max-w-2xl font-display text-3xl font-extrabold tracking-tight text-ink md:text-4xl">
            Um número, calculado uma vez.
          </h2>
          {/* The heading already says "calculado uma vez", so the lede does not
              get to say it again. It names what enters the calculation, then
              hands off to stage 3. */}
          <p className="mt-4 max-w-[65ch] leading-relaxed text-muted">
            A isenção de R$ 20.000, o prejuízo acumulado e a alíquota entram
            aqui. As três telas leem o resultado.
          </p>

          {/* Full stage width, like the extract sheet above it. This ledger is
              the page's argument; hiding it in a 48rem column while the intake
              runs off the screen made the evidence the quietest thing here. */}
          <div className="mt-10 overflow-hidden rounded-lg border border-hairline bg-panel">
            <div className="flex items-center justify-between gap-4 border-b border-hairline px-4 py-3">
              <p className="text-sm font-semibold text-ink">
                Apuração de julho de 2026
              </p>
              <span className="shrink-0 rounded-[4px] border border-hairline bg-paper px-1.5 py-0.5 font-mono text-[11px] font-medium uppercase leading-none tracking-wide text-muted">
                exemplo
              </span>
            </div>
            <dl>
              {APURACAO.map((r) => (
                <div
                  key={r.label}
                  className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1 border-b border-hairline/70 px-4 py-3"
                >
                  <dt className="min-w-0">
                    <span className={r.strong ? "font-semibold text-ink" : "text-ink"}>
                      {r.label}
                    </span>
                    {r.note && (
                      <span className="ml-2 font-mono text-[11px] text-muted">{r.note}</span>
                    )}
                  </dt>
                  <dd
                    className={`shrink-0 tnum ${
                      r.strong ? "font-semibold text-gain" : "text-ink"
                    }`}
                  >
                    {r.mark ? <Same tone="inherit">{r.value}</Same> : r.value}
                  </dd>
                </div>
              ))}
              {/* The chain terminates here, so the figure gets headline scale —
                  the step the design system reserves for landing figures. At
                  text-2xl the row that closes the page's whole argument was
                  smaller than a price in the plans table. */}
              <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-2 bg-paper px-4 py-5 md:py-6">
                <dt className="font-display text-xl font-extrabold tracking-tight text-ink">
                  DARF a pagar
                  <span className="ml-2 font-mono text-[11px] font-medium tracking-normal text-muted">
                    código 6015 · vence 31/08
                  </span>
                </dt>
                <dd className="shrink-0 font-display text-3xl font-extrabold tracking-tight text-ink tnum md:text-4xl">
                  {formatMoney(DARF)}
                </dd>
              </div>
            </dl>
          </div>

          {/* TLH and simulação are not two more features — they are the two
              places you can reach into this calculation before it closes. */}
          <div className="mt-8">
            <p className="font-mono text-[11px] font-medium uppercase tracking-[0.14em] text-muted">
              Duas portas antes de o mês fechar
            </p>
            <dl className="mt-4 grid gap-6 sm:grid-cols-2 sm:gap-10">
              <div>
                <dt className="text-sm font-semibold text-ink">
                  Colheita de prejuízo
                </dt>
                <dd className="mt-1 text-sm leading-relaxed text-muted">
                  Procura perdedores que derrubam essa base de{" "}
                  <span className="tnum">{formatMoney(BASE)}</span> — e a DARF
                  com ela.
                </dd>
              </div>
              <div>
                <dt className="text-sm font-semibold text-ink">Simulação</dt>
                <dd className="mt-1 text-sm leading-relaxed text-muted">
                  Mostra o imposto de um aporte ou de uma venda antes de você
                  executar na corretora.
                </dd>
              </div>
            </dl>
          </div>
        </Stage>

        {/* ── Stage 3 · the outlets ────────────────────────────────────────── */}
        <Stage stage="Saídas" marker="o mesmo número, três vezes">
          <h2 className="max-w-2xl font-display text-3xl font-extrabold tracking-tight text-ink md:text-4xl">
            {/* The mark at headline scale, where the section names the figure
                it is about — the same tint the three panels below use, stated
                once at full size instead of only in 14px prose. */}
            Onde <Same>{formatMoney(GANHO)}</Same> reaparece.
          </h2>
          {/* The moat clause is gone: what a competitor would have to build to
              copy this is our problem, not the reader's. What survives is the
              part they can check — three screens only agree if one calculation
              feeds them, which is what the panels below demonstrate. */}
          <p className="mt-4 max-w-[65ch] leading-relaxed text-muted">
            Cada concorrente vende uma dessas três telas. Elas só concordam
            entre si se vierem do mesmo cálculo.
          </p>

          {/* One instrument divided by hairlines, not three floating cards. */}
          <div className="mt-10 grid overflow-hidden rounded-lg border border-hairline bg-panel md:grid-cols-3">
            {OUTLETS.map(({ tag, title, Body }, i) => (
              // flex-col + a two-line floor on the heading + mt-auto on each
              // body's closing line: the three panels are one instrument, so
              // their bands have to sit on the same lines. Only the middle
              // reading differs — which is the point of the section.
              <div
                key={tag}
                className={`flex flex-col p-5 ${
                  i > 0 ? "border-t border-hairline md:border-l md:border-t-0" : ""
                }`}
              >
                <p className="font-mono text-[11px] font-medium uppercase tracking-[0.14em] text-muted">
                  {tag}
                </p>
                <h3 className="mt-2 text-base font-semibold leading-snug text-ink md:min-h-11">
                  {title}
                </h3>
                <Body />
              </div>
            ))}
          </div>
        </Stage>

        {/* ── Stage 4 · the fiscal year ────────────────────────────────────── */}
        <Stage stage="Ciclo" marker="o ano fiscal">
          <h2 className="max-w-2xl font-display text-3xl font-extrabold tracking-tight text-ink md:text-4xl">
            O motor roda o ano inteiro.
          </h2>
          {/* "A apuração é mensal e a DARF vence no mês seguinte" is exactly
              what the ruler below draws, annotation and all. The lede keeps
              only the part the ruler cannot say: what it costs to miss them. */}
          <p className="mt-4 max-w-[65ch] leading-relaxed text-muted">
            Quem abre o app só em abril perdeu onze apurações — e o prejuízo que
            elas compensariam.
          </p>

          {/* A ruler, not a checklist: what varies across the year is the
              declaração window and the one-month offset between an apuração and
              its DARF. "Every month" is a sentence, not twelve identical dots. */}
          {/* The ruler is 12 columns of whatever width there is, never a fixed
              44rem in a scroller: at 44rem on a phone the July marker and both
              annotations sat off-screen, so the section that argues the engine
              runs all year rendered as an empty box. The marks stay on the
              grid at every width; only the prose steps out of it below md. */}
          <div className="mt-12">
            {/* Baseline Grey, not Etched Rule: a ruler is a measurement, and
                the design system keeps those two tokens apart on purpose. */}
            <ol className="grid grid-cols-12 border-b border-axis pb-2">
              {MESES.map((m, i) => (
                <li
                  key={m}
                  className={`text-center font-mono text-[11px] font-medium uppercase ${
                    i === 6 || i === 7 ? "text-ink" : "text-muted"
                  }`}
                >
                  {m}
                </li>
              ))}
            </ol>

            {/* Ticks, so the thing the comment above calls a ruler is one. A
                row of labels over a rule is a caption; the marks are what make
                the year a scale you can point at a month on. */}
            <div aria-hidden className="grid grid-cols-12">
              {MESES.map((m, i) => (
                <span key={m} className="flex justify-center">
                  <span
                    className={`w-px ${
                      i === 6 || i === 7 ? "h-3 bg-ink" : "h-1.5 bg-axis"
                    }`}
                  />
                </span>
              ))}
            </div>

            {/* The one-month offset, on the same July the apuração above ran. */}
            <div className="grid grid-cols-12 items-center pt-3">
              <div className="col-start-7 col-span-2 flex items-center">
                <span aria-hidden className="h-1.5 w-1.5 shrink-0 rounded-full bg-navy" />
                <span aria-hidden className="h-px flex-1 bg-navy/40" />
                <span aria-hidden className="h-1.5 w-1.5 shrink-0 rounded-full bg-navy" />
              </div>
            </div>
            <div className="grid grid-cols-1 pt-2 md:grid-cols-12">
              <p className="text-sm leading-snug text-ink md:col-start-7 md:col-span-5 md:-ml-6">
                Apuração de julho
                <span className="block font-mono text-[11px] text-muted">
                  DARF vence 31/08 — um mês de folga, todo mês
                </span>
              </p>
            </div>

            {/* The window that actually opens and closes. */}
            <div className="mt-8 grid grid-cols-1 gap-3 md:grid-cols-12 md:gap-0">
              <div className="rounded-[4px] border border-hairline bg-navy/[0.05] px-3 py-2 md:col-start-1 md:col-span-5">
                <p className="text-sm text-ink">Declaração do IRPF</p>
                <p className="font-mono text-[11px] text-muted">
                  janeiro a maio, sobre o ano-base anterior
                </p>
              </div>
              <p className="text-sm leading-snug text-muted md:col-start-6 md:col-span-7 md:self-center md:pl-4">
                Nos outros sete meses a apuração continua.
              </p>
            </div>
          </div>
          {/* Dropped: a closing paragraph on isenção, prejuízo compensável por
              classe and gross-up de JCP. Densest jargon on the page, and the
              first two are already shown as rows in the apuração ledger. Only
              the JCP gross-up was a claim made nowhere else. */}
        </Stage>

        {/* ── Access ───────────────────────────────────────────────────────── */}
        {/* Off the signal path, and off its entrance too: the authored moment
            belongs to a stage arriving on the path. Repeating it here is what
            turns one moment into a page-wide tic. */}
        <section id="planos" className="scroll-mt-20 border-t border-hairline py-16 md:py-24">
          <div>
            <h2 className="max-w-2xl font-display text-3xl font-extrabold tracking-tight text-ink md:text-4xl">
              Preço simples, em reais
            </h2>
            {/* No plan ladder to explain, so the lede does the one thing rows
                cannot: say why the numbers differ from each other. */}
            <p className="mt-3 max-w-[65ch] text-muted">
              A carteira organizada é de graça, para sempre. O resto você liga um a um, e
              cada preço é o que aquele módulo custa de servidor: ler seu extrato uma vez
              é barato, rodar o motor sobre anos de histórico da B3 não é.
            </p>

            {/* One access panel divided by hairlines — a fatura, não uma grade de
                colunas: nada aqui se compara com nada, cada linha se liga sozinha. */}
            <div className="mt-10 overflow-hidden rounded-lg border border-hairline bg-panel">
              <div className="flex items-baseline justify-between gap-4 p-6">
                <div>
                  <h3 className="text-sm font-semibold text-ink">{CARTEIRA.name}</h3>
                  <p className="mt-1 max-w-[52ch] text-sm text-muted">{CARTEIRA.carga}</p>
                </div>
                <span className="shrink-0 font-mono text-[11px] font-medium uppercase tracking-[0.12em] text-navy">
                  Sempre incluída
                </span>
              </div>

              {AVULSOS.map((m) => (
                <div
                  key={m.id}
                  className="flex items-baseline justify-between gap-4 border-t border-hairline p-6"
                >
                  <div>
                    <h3 className="text-sm font-semibold text-ink">{m.name}</h3>
                    <p className="mt-1 max-w-[52ch] text-sm text-muted">{m.carga}</p>
                  </div>
                  <p className="shrink-0 font-display text-2xl font-extrabold tracking-tight text-ink tnum">
                    R$ {m.price}
                  </p>
                </div>
              ))}

              <div className="flex items-baseline justify-between gap-4 border-t border-hairline bg-navy/[0.03] p-6">
                <div>
                  <h3 className="text-sm font-semibold text-ink">{BUNDLE.name}</h3>
                  <p className="mt-1 max-w-[52ch] text-sm text-muted">{BUNDLE.carga}</p>
                  <p className="mt-1 font-mono text-[11px] text-muted">
                    os {AVULSOS.length} módulos avulsos somam R$ {TUDO} — o Empresarial é para quem
                    apura carteira de cliente
                  </p>
                </div>
                <p className="shrink-0 font-display text-2xl font-extrabold tracking-tight text-ink tnum">
                  R$ {BUNDLE.price}
                </p>
              </div>
            </div>

            <div className="mt-8 flex flex-wrap items-center gap-x-6 gap-y-3">
              <Link
                href="/login"
                className="rounded-md bg-navy px-6 py-3 text-sm font-semibold text-white transition-colors hover:bg-navy/90"
              >
                Começar grátis
              </Link>
              <p className="text-sm text-muted">
                Nenhum módulo é obrigatório. Cancele quando quiser.
              </p>
            </div>
          </div>
        </section>

        {/* ── Close ────────────────────────────────────────────────────────── */}
        <section className="border-t border-hairline py-20 md:py-28">
          <h2 className="max-w-2xl font-display text-3xl font-extrabold tracking-tight text-ink md:text-4xl">
            Sua carteira da B3, sem planilha.
          </h2>
          <p className="mt-4 max-w-xl text-muted">
            Leva um minuto para importar o primeiro extrato.
          </p>
          <Link
            href="/login"
            className="mt-8 inline-block rounded-md bg-navy px-6 py-3 text-sm font-semibold text-white transition-colors hover:bg-navy/90"
          >
            Começar grátis
          </Link>
        </section>
      </main>

      <footer className="border-t border-hairline">
        <div className="mx-auto flex max-w-6xl flex-col gap-4 px-4 py-8 md:flex-row md:items-center md:justify-between md:px-8">
          <Brand className="text-base" />
          <p className="max-w-md text-xs leading-relaxed text-muted">
            Ferramenta de organização de carteira e apuração. Não é recomendação
            de investimento nem serviço de contabilidade. Os números desta página
            são exemplos.
          </p>
          <Link
            href="/privacidade"
            className="text-xs text-muted underline underline-offset-2 hover:text-ink"
          >
            Política de Privacidade
          </Link>
        </div>
      </footer>
    </div>
  );
}
