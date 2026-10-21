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

/** Vendável, não só existente: `carteira` é módulo e é de graça, então um
 *  `?ligar=carteira` na URL só renderia um 400 do checkout. */
export const vendavel = (id: string) => id === EMPRESARIAL || AVULSOS.some((m) => m.id === id);
export const moduloDe = (id: string) => MODULOS.find((m) => m.id === id);
export const nomeDe = (id: string) => moduloDe(id)?.name ?? id;

/** Onde uma oferta manda o usuário: a tela de assinatura com `id` já no carrinho
 *  (`cartInicial` lê o `ligar`). Sem ele a pessoa chega a um carrinho vazio e
 *  tem de adivinhar qual dos módulos destrava o painel que ela estava vendo. */
export const ligarHref = (id: string) => `/assinatura?ligar=${encodeURIComponent(id)}`;

export const somaModulos = (ids: string[]) =>
  AVULSOS.filter((m) => ids.includes(m.id)).reduce((s, m) => s + m.price, 0);

/** O preço do carrinho como a API vai cobrá-lo: o bundle substitui a soma. */
export const precoDoCarrinho = (ids: string[]) =>
  ids.includes(EMPRESARIAL) ? BUNDLE.price : somaModulos(ids);

export const mesmoConjunto = (a: string[], b: string[]) =>
  a.length === b.length && a.every((m) => b.includes(m));

/** Marcar ou desmarcar um módulo no carrinho. O bundle não convive com nada: a API
 *  400a um carrinho misto (cobraria o mesmo acesso duas vezes), então a tela não
 *  pode nem montar um. */
export const alternaModulo = (cart: string[], id: string): string[] => {
  if (cart.includes(id)) return cart.filter((m) => m !== id);
  if (id === EMPRESARIAL) return [EMPRESARIAL];
  return [...cart.filter((m) => m !== EMPRESARIAL), id];
};

/** O que a tela de assinatura mostra marcado antes de o usuário tocar em nada: o
 *  que já é cobrado, mais o módulo que o paywall pediu para ligar. Via
 *  `alternaModulo` porque `?ligar=empresarial` também tem de expulsar os avulsos;
 *  um `ligar` que não é módulo é ignorado (a URL vem de fora). */
export const cartInicial = (billed: string[] | undefined, ligar: string | null) => {
  const base = billed ?? [];
  return ligar && vendavel(ligar) && !base.includes(ligar) ? alternaModulo(base, ligar) : base;
};

/** "É o que você já assina" só é verdade quando existe assinatura para alterar.
 *  Um `incomplete` (3DS pendente), `unpaid` ou `paused` guarda o conjunto cobrado
 *  sem dar acesso: travar o botão nele deixaria o cliente sem como comprar o que
 *  ele justamente não tem. */
export const nadaAMudar = (
  cart: string[],
  sub?: { changeable: boolean; billed_modules: string[] },
) => !!sub?.changeable && mesmoConjunto(sub.billed_modules, cart);
