// ── Variação tudo · "um lugar para tudo na B3" ────────────────────────────────
// "Tudo num lugar" tem uma expressão default — quatro cartões com ícone — que é
// exatamente a grade que o contrato de app/page.tsx existe para recusar, e que
// diz o oposto do que promete: quatro cartões são quatro produtos.
//
// Então a composição carrega a tese: UM painel, com uma faixa de entrada que
// atravessa a largura inteira e quatro leituras embaixo dela, separadas só por
// fios. Um instrumento com quatro mostradores, não quatro instrumentos. O que
// prova "um lugar" é a entrada ser uma só — os mesmos dois relatórios respondem
// as quatro perguntas.
//
// E as quatro perguntas são as linhas da fatura (lib/modules.ts), então o bloco de
// planos entra como parte do argumento em vez de apêndice.
import { formatMoney, formatPercent } from "@/lib/format";
import { ALIQUOTA, DARF } from "@/lib/apuracao";
import {
  AllocationBars,
  BenchCard,
  BenchFigures,
  Close,
  Cta,
  CUSTO_PP,
  ExemploChip,
  Footer,
  Nav,
  quadEdges,
  TRUST,
} from "../shared";
import { Assinatura } from "./Assinatura";

// Rótulos de UI, não clientes de verdade: o produto é pré-lançamento e inventar
// nomes de cliente numa landing seria fabricar prova social.
const CARTEIRAS = ["Minha carteira", "Cliente A", "Cliente B", "+ nova"];

const LEITURAS = [
  {
    tag: "Carteira",
    line: "Preço médio ajustado por eventos, entre corretoras.",
    Visual: AllocationBars,
  },
  {
    tag: "Imposto",
    line: `Apuração mensal com isenção, prejuízo e ${formatPercent(ALIQUOTA)} de alíquota.`,
    Visual: () => (
      <div className="flex h-full flex-col items-start justify-center gap-1 rounded-[4px] bg-paper px-3">
        <p className="font-mono text-[11px] uppercase tracking-[0.14em] text-muted">
          DARF 6015 · vence 31/08
        </p>
        <p className="font-display text-3xl font-extrabold text-ink tnum">{formatMoney(DARF)}</p>
        <ExemploChip />
      </div>
    ),
  },
  {
    tag: "Estratégias",
    line: "Backtest sobre o histórico da B3, com o imposto descontado.",
    Visual: BenchFigures,
  },
  {
    tag: "Clientes",
    // A troca de carteira é a claim do plano Empresarial, então aparece como o
    // artefato que ela é — um seletor — e não como um bullet.
    line: "Cada uma com IRPF e rebalanceamento próprios, dados isolados.",
    Visual: () => (
      <div className="flex h-full flex-col justify-center gap-2 rounded-[4px] bg-paper px-3">
        {CARTEIRAS.map((c, i) => (
          <p
            key={c}
            className={`truncate rounded-[4px] border px-2 py-1 font-mono text-[11px] uppercase tracking-wide ${
              i === 0
                ? "border-navy/30 bg-navy/[0.06] font-medium text-ink"
                : "border-hairline text-muted"
            }`}
          >
            {c}
          </p>
        ))}
      </div>
    ),
  },
];

export default function VarianteTudo() {
  return (
    <div className="min-h-screen bg-paper">
      <Nav />
      <main className="mx-auto max-w-6xl px-4 md:px-8">
        <section className="pt-14 pb-8 md:pt-20">
          <div className="max-w-3xl">
            <p className="font-mono text-[11px] font-medium uppercase tracking-[0.14em] text-muted">
              Carteira · imposto · estratégias · clientes
            </p>
            <h1 className="mt-4 font-display text-[clamp(2.25rem,5.5vw,3.75rem)] font-extrabold leading-[1.05] tracking-tight text-ink">
              Tudo o que você faz na B3, num lugar.
            </h1>
            <p className="mt-5 max-w-[56ch] text-lg leading-relaxed text-muted">
              Os mesmos dois relatórios que você baixa da Área do Investidor viram carteira, imposto
              do mês e backtest — e as carteiras dos seus clientes, cada uma isolada.
            </p>
          </div>

          {/* Um painel: a entrada atravessa a largura toda e as quatro leituras
              ficam embaixo dela. O que junta as quatro é a entrada ser uma. */}
          <div className="mt-10 overflow-hidden rounded-lg border border-hairline bg-panel">
            <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-2 border-b border-hairline bg-paper px-5 py-3.5">
              <p className="font-mono text-[11px] font-medium uppercase tracking-[0.14em] text-ink">
                Entra · 2 relatórios da B3 · 24 tipos de movimentação
              </p>
              <ExemploChip />
            </div>
            <dl className="grid sm:grid-cols-2 xl:grid-cols-4">
              {LEITURAS.map(({ tag, line, Visual }, i) => (
                <div key={tag} className={`flex flex-col gap-4 p-5 ${quadEdges(i)}`}>
                  <dt className="font-mono text-[11px] font-medium uppercase tracking-[0.14em] text-muted">
                    {tag}
                  </dt>
                  {/* Mesmo piso das outras faixas: as quatro barras de alocação
                      são o visual mais alto, então todos reservam a altura. */}
                  <div className="h-32">
                    <Visual />
                  </div>
                  <dd className="mt-auto text-sm leading-relaxed text-ink">{line}</dd>
                </div>
              ))}
            </dl>
          </div>

          {/* O argumento "é um motor" saiu daqui: ele agora tem a seção abaixo,
              com a curva que o prova. Repetir a frase aqui e lá era prosa. */}
          <div className="mt-8 flex flex-wrap items-center gap-3">
            <Cta>Começar grátis</Cta>
            <span className="text-sm text-muted">{TRUST}</span>
          </div>
        </section>

        {/* Sem ProofBand aqui: os dois números dela — 2 relatórios e 24 tipos —
            já estão na faixa de entrada do painel, que é onde eles significam
            algo. Repetir os mesmos dois logo abaixo seria enchimento. */}

        {/* Uma evidência só, e a mais difícil de copiar: a curva bruta contra a
            líquida. O painel acima afirma que é um motor; esta seção é onde a
            afirmação fica visível, porque a distância entre as duas linhas é a
            mesma alíquota que produz a DARF do mostrador de imposto. */}
        <section className="pt-16">
          <h2 className="max-w-2xl font-display text-3xl font-extrabold tracking-tight text-ink md:text-4xl">
            Um motor, não quatro integrações.
          </h2>
          <p className="mt-4 max-w-[58ch] text-muted">
            A venda que fecha a posição na carteira é a mesma que entra na apuração do mês — e a mesma
            que o backtest tributa. Por isso o retorno que aparece aqui é o que sobra depois do
            imposto, e não o número que nenhum backtest estrangeiro sabe descontar.
          </p>
          <BenchCard hero className="mt-8" />
          <p className="mt-4 max-w-[60ch] text-sm leading-relaxed text-ink">
            Neste exemplo o imposto custa <span className="font-semibold tnum">{CUSTO_PP}</span> de
            retorno em dois anos. Nenhuma das quatro telas mostra essa linha sozinha.
          </p>
        </section>

        <Assinatura />
        <Close
          title="Um extrato para as quatro perguntas."
          line="A carteira organizada é de graça, para sempre. O resto você liga quando usar."
        />
      </main>
      <Footer />
    </div>
  );
}
