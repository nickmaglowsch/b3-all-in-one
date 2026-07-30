// O catálogo de assinatura: um módulo por grupo de rotas, precificado pelo que o
// servidor gasta. Não existe escada — `bench` não implica `irpf`, e por isso o
// entitlement é um conjunto, não um posto (platform_app/billing.MODULES é a
// autoridade sobre acesso; este arquivo só é dono do preço e da cópia).
//
// Os ids têm de bater com o backend: são eles que voltam no 402
// (`required_module`) e no GET /billing. `lib/modules.test.ts` trava a lista.
//
// A regra que decide onde um módulo começa e termina é o custo, não o nome do
// recurso: colheita de prejuízo, simulação de venda e rebalanceamento precisam
// exatamente da mesma coisa — o preço de hoje de cada papel — então são um módulo
// (`mercado`) e não três.

export type Modulo = {
  id: string;
  name: string;
  /** Reais por mês. 0 = base, sempre incluída. */
  price: number;
  /** O que o servidor gasta — a razão do preço, em uma linha. */
  carga: string;
};

/** A base, sempre entitulada: os extratos importados são do tenant, e um cartão
 *  vencido não é motivo para esconder a carteira dele. */
export const CARTEIRA = "carteira";
/** O único pacote fechado: todos os módulos, para carteiras de clientes
 *  ilimitadas. Não entra num carrinho junto com outro módulo — cobraria o mesmo
 *  acesso duas vezes, e a API recusa. */
export const EMPRESARIAL = "empresarial";

export const MODULOS: Modulo[] = [
  {
    id: CARTEIRA,
    name: "Carteira",
    price: 0,
    carga: "Lê o extrato uma vez e guarda. Depois disso nada roda.",
  },
  {
    id: "irpf",
    name: "IRPF e DARF",
    price: 19,
    carga: "Reapura os seus próprios lançamentos, mês a mês. Sem dado externo.",
  },
  {
    id: "mercado",
    name: "Preço de mercado",
    price: 29,
    carga:
      "Precisa do preço de hoje de cada papel: colheita de prejuízo, simulação de venda e distância do alvo de rebalanceamento.",
  },
  {
    id: "indice",
    name: "Carteira contra IBOV e CDI",
    price: 12,
    carga: "Refaz a série diária da carteira e dos dois benchmarks todo dia.",
  },
  {
    id: "bench",
    name: "Backtests e estratégias próprias",
    price: 49,
    carga: "Roda o motor sobre anos de histórico da B3 a cada execução sua.",
  },
  {
    id: EMPRESARIAL,
    name: "Empresarial",
    price: 249,
    carga:
      "Todos os módulos, para carteiras de clientes ilimitadas — cada uma apurada por inteiro.",
  },
];

export const BASE = MODULOS[0];
/** O que se liga um a um: sem a base (grátis) e sem o bundle (que é todos). */
export const AVULSOS = MODULOS.filter((m) => m.id !== CARTEIRA && m.id !== EMPRESARIAL);
export const BUNDLE = MODULOS.find((m) => m.id === EMPRESARIAL)!;

/** Tudo ligado avulso. O piso que o Empresarial tem de superar para o preço dele
 *  fazer sentido em vez de parecer punição. */
export const TUDO = AVULSOS.reduce((s, m) => s + m.price, 0);

export const moduloDe = (id: string) => MODULOS.find((m) => m.id === id);
export const nomeDe = (id: string) => moduloDe(id)?.name ?? id;

export const somaModulos = (ids: string[]) =>
  AVULSOS.filter((m) => ids.includes(m.id)).reduce((s, m) => s + m.price, 0);

/** O preço do carrinho como a API vai cobrá-lo: o bundle substitui a soma. */
export const precoDoCarrinho = (ids: string[]) =>
  ids.includes(EMPRESARIAL) ? BUNDLE.price : somaModulos(ids);

/** Marcar ou desmarcar um módulo no carrinho. O bundle não convive com nada: a API
 *  400a um carrinho misto (cobraria o mesmo acesso duas vezes), então a tela não
 *  pode nem montar um. */
export const alternaModulo = (cart: string[], id: string): string[] => {
  if (cart.includes(id)) return cart.filter((m) => m !== id);
  if (id === EMPRESARIAL) return [EMPRESARIAL];
  return [...cart.filter((m) => m !== EMPRESARIAL), id];
};
