// Assinatura montada módulo a módulo, precificada por custo de servidor.
//
// Isto NÃO é lib/plans.ts. `plans.ts` descreve a escada que a API vende hoje
// (platform_app/billing.LADDER: gratis < investidor < trader < enterprise) e é o
// que /assinatura, /conta e o paywall precisam falar. Este arquivo descreve o
// modelo que a landing propõe: sem escada, você liga o que usa.
//
// Cada módulo é um grupo de rotas que já existe e já é gated, então a lista não
// pode virar ficção — mas o *gate* de hoje é por índice na escada
// (billing.require_plan compara LADDER.index), e por isso vender isto de verdade
// precisa de entitlement por conjunto, não por rank. Ver o commit que introduziu
// este arquivo.
//
// O preço segue o que o servidor faz, não o valor percebido: guardar um extrato
// custa uma vez; rodar o motor sobre dez anos de histórico custa a cada clique.

export type Modulo = {
  id: string;
  name: string;
  /** Reais por mês. 0 = base, sempre incluída. */
  price: number;
  /** O que o servidor gasta — a razão do preço, em uma linha. */
  carga: string;
};

export const MODULOS: Modulo[] = [
  {
    // platform_app/main.py: rotas de extrato, posições e proventos, sem gate.
    id: "carteira",
    name: "Carteira",
    price: 0,
    carga: "Lê o extrato uma vez e guarda. Depois disso nada roda.",
  },
  {
    // irpf_router — reprocessa os trades que já estão no banco.
    id: "irpf",
    name: "IRPF e DARF",
    price: 19,
    carga: "Reapura os seus próprios lançamentos, mês a mês. Sem dado externo.",
  },
  {
    // tlh_router + whatif_router — dependem do preço de hoje de cada papel.
    id: "prejuizo",
    name: "Colheita de prejuízo e simulação",
    price: 19,
    carga: "Precisa do preço de hoje de cada papel que você tem para simular a venda.",
  },
  {
    // portfolio_index_router — série diária da carteira contra os benchmarks.
    id: "indice",
    name: "Carteira contra IBOV e CDI",
    price: 12,
    carga: "Refaz a série diária da carteira e dos dois benchmarks todo dia.",
  },
  {
    // backtests_router — o motor sobre o histórico inteiro, por execução.
    id: "bench",
    name: "Backtests",
    price: 49,
    carga: "Roda o motor sobre anos de histórico da B3 a cada execução sua.",
  },
  {
    // As rotas de overlay: o mesmo backtest repetido por regime de mercado.
    id: "overlays",
    name: "Overlays por regime",
    price: 39,
    carga: "Cada regime é o backtest inteiro outra vez, sob outra condição de mercado.",
  },
];

export const BASE = MODULOS[0];
export const PAGOS = MODULOS.slice(1);

/** Tudo ligado, sem o Empresarial. O piso que o Empresarial tem de superar. */
export const TUDO = PAGOS.reduce((s, m) => s + m.price, 0);

// O único plano fechado que sobra, porque não é um módulo: é todos os módulos
// vezes N carteiras. Mais caro que ligar tudo, e é por isso que o preço faz
// sentido em vez de parecer punição.
export const EMPRESARIAL = {
  name: "Empresarial",
  price: 249,
  carga: "Todos os módulos, para carteiras de clientes ilimitadas — cada uma apurada por inteiro.",
};

export const somaModulos = (ids: string[]) =>
  PAGOS.filter((m) => ids.includes(m.id)).reduce((s, m) => s + m.price, 0);
