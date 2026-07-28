import Link from "next/link";
import { Brand } from "@/components/Brand";
import { Reveal } from "@/components/landing/Motion";
import { formatMoney, formatPercent } from "@/lib/format";
import { PLANS } from "@/lib/plans";
import { IconCheck } from "@/components/icons";

// ── Direction contract ────────────────────────────────────────────────────────
// THESIS: one tax engine, shown in cross-section. Refuses the SaaS stack this
//   page used to be — hero + icon-card grid + alternating showcase + pricing —
//   which claimed the loop in an h2 and then demonstrated three separate tools.
// OWN-WORLD: unchanged Quantax Instrument Panel (frontend/DESIGN.md). Bench
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
  marker,
  children,
}: {
  id?: string;
  marker: string;
  children: React.ReactNode;
}) {
  return (
    <Reveal>
      <section id={id} className="scroll-mt-20 grid grid-cols-[2rem_1fr] md:grid-cols-[7rem_1fr]">
        <div className="relative border-r border-hairline">
          {/* Sits on the rule, centred on the marker's line — the signal
              arriving at this stage. */}
          <span
            aria-hidden
            className="stage-node absolute -right-[4.5px] top-[4.4rem] h-2 w-2 rounded-full border border-hairline bg-paper md:top-[6.45rem]"
          />
        </div>
        <div className="min-w-0 py-16 pl-5 pr-4 md:py-24 md:pl-12 md:pr-8">
          <p className="mb-4 font-mono text-[11px] font-medium uppercase tracking-[0.14em] text-muted">
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

// A sample of the 16 MOV_CATEGORY labels — these strings are the parser's keys.
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
        <span className="shrink-0 rounded-[4px] border border-hairline bg-paper px-1.5 py-0.5 font-mono text-[11px] font-medium uppercase leading-none tracking-wide text-muted">
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
}[] = [
  { label: "Vendas de ações no mês", note: "4 operações, dos extratos", value: formatMoney(VENDAS) },
  { label: "Isenção mensal de R$ 20.000", note: "excedida — o mês é tributável", value: "não se aplica" },
  { label: "Custo pelo preço médio", note: "ajustado por desdobros e amortizações", value: `− ${formatMoney(CUSTO)}` },
  { label: "Ganho apurado", value: formatMoney(GANHO, true), strong: true },
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
function Same({ children }: { children: React.ReactNode }) {
  return (
    <mark className="rounded-[4px] bg-navy/[0.08] px-1 font-medium text-ink">
      {children}
    </mark>
  );
}

function OutletBench() {
  const bruto = [100, 102, 101, 105, 108, 107, 112, 115, 118.2];
  const liquido = bruto.map((v) => 100 + (v - 100) * (1 - ALIQUOTA));
  const all = [...bruto, ...liquido];
  const lo = Math.min(...all);
  const hi = Math.max(...all);
  return (
    <>
      <div className="mt-5 h-28">
        <svg viewBox="0 0 100 40" preserveAspectRatio="none" className="h-full w-full" aria-hidden>
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
            <span aria-hidden className="h-0.5 w-4 rounded-full bg-axis" />
            Retorno bruto
          </dt>
          <dd className="tnum text-muted">+18,2%</dd>
        </div>
        <div className="flex items-baseline justify-between gap-3">
          <dt className="flex items-center gap-2 text-muted">
            <span aria-hidden className="h-0.5 w-4 rounded-full bg-series-1" />
            Depois do imposto
          </dt>
          <dd className="tnum font-medium text-ink">+15,5%</dd>
        </div>
      </dl>
      <p className="mt-4 text-sm leading-relaxed text-muted">
        A distância entre as duas curvas é a <Same>mesma alíquota de 15%</Same> —
        descontada operação a operação, não estimada no fim.
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
      <dl className="mt-5 space-y-3">
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
              <span className={o.valor > 0 ? "text-gain" : "text-loss"}>{o.acao}</span>{" "}
              <span className="font-mono text-xs uppercase">{o.ticker}</span>{" "}
              <span className="text-muted">· {o.motivo}</span>
            </span>
            <span className="shrink-0 tnum text-muted">{formatMoney(o.valor, true)}</span>
          </li>
        ))}
      </ul>
      <p className="mt-4 text-sm leading-relaxed text-muted">
        O plano já sabe que essa venda realiza{" "}
        <Same>{formatMoney(GANHO)}</Same> e o que isso custa em imposto antes de
        sugerir a ordem.
      </p>
    </>
  );
}

function OutletIrpf() {
  return (
    <>
      <div className="mt-5 rounded-lg border border-hairline bg-paper p-4">
        <p className="font-mono text-[11px] font-medium uppercase tracking-[0.14em] text-muted">
          DARF · código 6015
        </p>
        <p className="mt-1 font-display text-3xl font-extrabold text-ink tnum">
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
      <p className="mt-4 text-sm leading-relaxed text-muted">
        O mesmo ganho entra na ficha de renda variável do mês, pronto para
        conferir contra o informe e copiar na declaração.
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
    <div className="min-h-screen bg-paper">
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
        <Stage marker="Entrada · o extrato da B3">
          <h1 className="max-w-3xl font-display text-4xl font-extrabold leading-[1.05] tracking-tight text-ink md:text-6xl">
            Um motor de imposto. Três saídas.
          </h1>
          <p className="mt-6 max-w-[65ch] text-lg leading-relaxed text-muted">
            O mesmo cálculo que pontua um backtest apura sua carteira e emite sua
            DARF. Você sobe os extratos da B3 — daí em diante é um número só,
            seguido até a declaração.
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
            Sem cartão de crédito. Só leitura dos seus extratos — nada executa na
            sua corretora.
          </p>

          {/* The intake itself, at full width. */}
          <div className="mt-14 md:-mr-8">
            <ExtractSheet />
          </div>

          <div className="mt-6 grid gap-6 md:grid-cols-[auto_1fr] md:gap-10">
            <p className="max-w-xs text-sm leading-relaxed text-muted">
              <span className="text-ink">Dois relatórios</span> da Área do
              Investidor entram inteiros: negociação e movimentação. O parser
              reconhece a planilha pelas colunas, não pelo nome do arquivo.
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
        <Stage id="motor" marker="Apuração · o núcleo">
          <h2 className="max-w-2xl font-display text-3xl font-extrabold tracking-tight text-ink md:text-4xl">
            Um número, calculado uma vez.
          </h2>
          <p className="mt-4 max-w-[65ch] leading-relaxed text-muted">
            É aqui que a isenção de R$ 20.000, o prejuízo acumulado e a alíquota
            se encontram. Nada disso roda três vezes em três lugares — roda uma,
            e as três telas leem o resultado.
          </p>

          <div className="mt-10 max-w-3xl overflow-hidden rounded-lg border border-hairline bg-panel">
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
                    {r.value}
                  </dd>
                </div>
              ))}
              <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1 bg-paper px-4 py-4">
                <dt className="font-display text-lg font-extrabold tracking-tight text-ink">
                  DARF a pagar
                  <span className="ml-2 font-mono text-[11px] font-medium tracking-normal text-muted">
                    código 6015 · vence 31/08
                  </span>
                </dt>
                <dd className="shrink-0 font-display text-2xl font-extrabold text-ink tnum">
                  {formatMoney(DARF)}
                </dd>
              </div>
            </dl>
          </div>

          {/* TLH and simulação are not two more features — they are the two
              places you can reach into this calculation before it closes. */}
          <div className="mt-8 max-w-3xl">
            <p className="font-mono text-[11px] font-medium uppercase tracking-[0.14em] text-muted">
              Duas portas antes de o mês fechar
            </p>
            <dl className="mt-4 grid gap-6 sm:grid-cols-2">
              <div>
                <dt className="text-sm font-semibold text-ink">
                  Colheita de prejuízo
                </dt>
                <dd className="mt-1 text-sm leading-relaxed text-muted">
                  O TLH procura perdedores que derrubam essa base de{" "}
                  <span className="tnum">{formatMoney(BASE)}</span> — e a DARF
                  junto com ela.
                </dd>
              </div>
              <div>
                <dt className="text-sm font-semibold text-ink">Simulação</dt>
                <dd className="mt-1 text-sm leading-relaxed text-muted">
                  Passa um aporte ou uma venda pelo mesmo cálculo e mostra o
                  imposto antes de você executar na corretora.
                </dd>
              </div>
            </dl>
          </div>
        </Stage>

        {/* ── Stage 3 · the outlets ────────────────────────────────────────── */}
        <Stage marker="Saídas · o mesmo número, três vezes">
          <h2 className="max-w-2xl font-display text-3xl font-extrabold tracking-tight text-ink md:text-4xl">
            Onde {formatMoney(GANHO)} reaparece.
          </h2>
          <p className="mt-4 max-w-[65ch] leading-relaxed text-muted">
            Concorrentes vendem uma dessas três telas cada um. Elas só podem
            concordar entre si se vierem do mesmo cálculo — e é isso que nenhum
            deles pode copiar sem rodar um simulador e um motor de IRPF no mesmo
            código.
          </p>

          {/* One instrument divided by hairlines, not three floating cards. */}
          <div className="mt-10 grid overflow-hidden rounded-lg border border-hairline bg-panel md:grid-cols-3">
            {OUTLETS.map(({ tag, title, Body }, i) => (
              <div
                key={tag}
                className={`p-5 ${
                  i > 0 ? "border-t border-hairline md:border-l md:border-t-0" : ""
                }`}
              >
                <p className="font-mono text-[11px] font-medium uppercase tracking-[0.14em] text-muted">
                  {tag}
                </p>
                <h3 className="mt-2 text-base font-semibold leading-snug text-ink">
                  {title}
                </h3>
                <Body />
              </div>
            ))}
          </div>
        </Stage>

        {/* ── Stage 4 · the fiscal year ────────────────────────────────────── */}
        <Stage marker="Ciclo · o ano fiscal">
          <h2 className="max-w-2xl font-display text-3xl font-extrabold tracking-tight text-ink md:text-4xl">
            O motor roda o ano inteiro.
          </h2>
          <p className="mt-4 max-w-[65ch] leading-relaxed text-muted">
            A apuração é mensal e a DARF vence no mês seguinte. Quem só abre o
            app em abril já perdeu onze apurações — e o prejuízo que elas
            compensariam.
          </p>

          {/* A ruler, not a checklist: what varies across the year is the
              declaração window and the one-month offset between an apuração and
              its DARF. "Every month" is a sentence, not twelve identical dots. */}
          <div className="mt-12 overflow-x-auto pb-2">
            <div className="min-w-[44rem]">
              <ol className="grid grid-cols-12 border-b border-hairline pb-2">
                {MESES.map((m, i) => (
                  <li
                    key={m}
                    className={`text-center font-mono text-[11px] font-medium uppercase tracking-wide ${
                      i === 6 || i === 7 ? "text-ink" : "text-muted"
                    }`}
                  >
                    {m}
                  </li>
                ))}
              </ol>

              {/* The one-month offset, on the same July the apuração above ran. */}
              <div className="grid grid-cols-12 items-center pt-4">
                <div className="col-start-7 col-span-2 flex items-center">
                  <span aria-hidden className="h-1.5 w-1.5 shrink-0 rounded-full bg-navy" />
                  <span aria-hidden className="h-px flex-1 bg-navy/40" />
                  <span aria-hidden className="h-1.5 w-1.5 shrink-0 rounded-full bg-navy" />
                </div>
              </div>
              <div className="grid grid-cols-12 pt-2">
                <p className="col-start-7 col-span-3 -ml-6 text-sm leading-snug text-ink">
                  Apuração de julho
                  <span className="block font-mono text-[11px] text-muted">
                    DARF vence 31/08 — um mês de folga, todo mês
                  </span>
                </p>
              </div>

              {/* The window that actually opens and closes. */}
              <div className="mt-8 grid grid-cols-12">
                <div className="col-start-1 col-span-5 rounded-[4px] border border-hairline bg-navy/[0.05] px-3 py-2">
                  <p className="text-sm text-ink">Declaração do IRPF</p>
                  <p className="font-mono text-[11px] text-muted">
                    janeiro a maio, sobre o ano-base anterior
                  </p>
                </div>
                <p className="col-start-6 col-span-7 self-center pl-4 text-sm leading-snug text-muted">
                  Nos outros sete meses o concorrente sazonal fecha. A apuração
                  não.
                </p>
              </div>
            </div>
          </div>

          <p className="mt-8 max-w-[65ch] text-sm leading-relaxed text-muted">
            Isenção de R$ 20.000/mês em ações, prejuízo compensável por classe e
            gross-up de JCP são regras estruturais de todo mês — não exceções.
          </p>
        </Stage>

        {/* ── Access ───────────────────────────────────────────────────────── */}
        <section id="planos" className="scroll-mt-20 border-t border-hairline py-16 md:py-24">
          <Reveal>
            <h2 className="max-w-2xl font-display text-3xl font-extrabold tracking-tight text-ink md:text-4xl">
              Preço simples, em reais
            </h2>
            <p className="mt-3 max-w-[65ch] text-muted">
              Comece de graça e assine quando precisar de imposto e estratégia.
              Cancele quando quiser.
            </p>

            {/* One access panel divided by hairlines. */}
            <div className="mt-10 grid overflow-hidden rounded-lg border border-hairline bg-panel md:grid-cols-2 xl:grid-cols-4">
              {PLANS.map((plan, i) => (
                <div
                  key={plan.id}
                  className={`flex flex-col p-6 ${
                    i > 0 ? "border-t border-hairline md:border-t-0 xl:border-l" : ""
                  } ${i === 1 ? "md:border-l" : ""} ${i >= 2 ? "md:border-t" : ""} ${
                    i === 3 ? "md:border-l" : ""
                  } ${plan.featured ? "bg-navy/[0.03]" : ""}`}
                >
                  <div className="flex items-baseline justify-between gap-2">
                    <h3 className="text-sm font-semibold text-ink">{plan.name}</h3>
                    {plan.featured && (
                      <span className="shrink-0 font-mono text-[11px] font-medium uppercase tracking-[0.12em] text-navy">
                        Mais popular
                      </span>
                    )}
                  </div>
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
              ))}
            </div>
          </Reveal>
        </section>

        {/* ── Close ────────────────────────────────────────────────────────── */}
        <section className="border-t border-hairline py-20 md:py-28">
          <Reveal>
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
          </Reveal>
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
