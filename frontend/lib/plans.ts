// The tier ladder, shared by the landing pricing section, /assinatura and the
// paywall dialog. Ids match platform_app/billing.LADDER — the API is the source of
// truth for entitlement, this file only owns the copy.

export const PLAN_IDS = ["gratis", "investidor", "trader", "enterprise"] as const;
export type PlanId = (typeof PLAN_IDS)[number];

export type Plan = {
  id: PlanId;
  name: string;
  price: string;
  cadence: string;
  tagline: string;
  features: string[];
  cta: string;
  featured?: boolean;
};

export const PLANS: Plan[] = [
  {
    id: "gratis",
    name: "Grátis",
    price: "R$ 0",
    cadence: "para sempre",
    tagline: "Para organizar a carteira sem custo.",
    features: [
      "Importação de extratos da B3",
      "Carteira e posições consolidadas",
      "Histórico de proventos",
      "1 conta",
    ],
    cta: "Começar grátis",
  },
  {
    id: "investidor",
    name: "Investidor",
    price: "R$ 29",
    cadence: "/mês",
    tagline: "Para quem declara e otimiza o imposto.",
    features: [
      "Tudo do plano Grátis",
      "IRPF automático + DARF",
      "Colheita de prejuízo (TLH)",
      "Estratégia e simulação",
      "Contas ilimitadas",
    ],
    cta: "Assinar Investidor",
    featured: true,
  },
  {
    id: "trader",
    name: "Trader",
    price: "R$ 79",
    cadence: "/mês",
    tagline: "Para quem testa e refina estratégias.",
    features: [
      "Tudo do plano Investidor",
      "Bench e backtests históricos",
      "Estratégias próprias",
      "Overlays por regime de mercado",
      "Suporte prioritário",
    ],
    cta: "Assinar Trader",
  },
  {
    id: "enterprise",
    name: "Empresarial",
    price: "R$ 249",
    cadence: "/mês",
    tagline: "Para quem cuida da carteira dos outros.",
    features: [
      "Tudo do plano Trader",
      "Carteiras de clientes ilimitadas",
      "Troca de carteira sem trocar de login",
      "IRPF e rebalanceamento por cliente",
      "Dados isolados entre clientes",
    ],
    cta: "Assinar Empresarial",
  },
];

export const planOf = (id: string) => PLANS.find((p) => p.id === id);
export const planName = (id: string) => planOf(id)?.name ?? id;
export const planRank = (id: string) => PLAN_IDS.indexOf(id as PlanId);
