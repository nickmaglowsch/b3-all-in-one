import type { Metadata } from "next";
import Link from "next/link";
import { Brand } from "@/components/Brand";

// Kept in one place so the page, /conta and the API (platform_app/privacy.CONTACT)
// publish the same channel.
const CONTACT = "privacidade@quantax.com.br";

// Change this when the text below changes — a policy with a stale date is worse
// than one with no date.
const UPDATED = "25 de julho de 2026";

export const metadata: Metadata = {
  title: "Política de Privacidade · Quantax",
  description:
    "Como a Quantax trata dados pessoais, sob a Lei Geral de Proteção de Dados (LGPD, Lei 13.709/2018).",
};

export default function PrivacidadePage() {
  return (
    <div className="min-h-screen bg-paper">
      <header className="border-b border-hairline">
        <div className="mx-auto flex max-w-3xl items-center justify-between px-4 py-5 md:px-8">
          <Link href="/">
            <Brand className="text-base" />
          </Link>
          <Link href="/conta" className="text-sm text-muted hover:text-ink">
            Meus dados
          </Link>
        </div>
      </header>

      <main className="mx-auto max-w-3xl space-y-10 px-4 py-12 md:px-8">
        <div>
          <h1 className="font-display text-3xl font-extrabold tracking-tight text-ink">
            Política de Privacidade
          </h1>
          <p className="mt-2 text-sm text-muted">
            Última atualização: {UPDATED}. Disponível em português do Brasil, o
            único idioma da plataforma hoje.
          </p>
        </div>

        <Section title="1. Quem é o controlador">
          <p>
            A Quantax é a controladora dos dados pessoais tratados na plataforma:
            é ela quem decide por que e como esses dados são usados. Para falar
            com o encarregado pelo tratamento de dados (art. 41 da LGPD), escreva
            para <Mail />.
          </p>
          <p>
            Quando um escritório ou assessor usa o plano Empresarial para
            administrar carteiras de terceiros, é ele o controlador dos dados dos
            seus clientes; a Quantax atua como operadora, tratando esses dados
            apenas conforme as instruções dele.
          </p>
        </Section>

        <Section title="2. Que dados tratamos">
          <ul className="list-disc space-y-1.5 pl-5">
            <li>
              <strong className="text-ink">Cadastro:</strong> email e, se você
              entrar com o Google, o identificador e o email da sua conta Google.
            </li>
            <li>
              <strong className="text-ink">Extratos da B3 que você envia:</strong>{" "}
              negociações, movimentações, proventos, posições e a corretora de
              cada operação — os arquivos originais e os dados extraídos deles.
            </li>
            <li>
              <strong className="text-ink">O que você configura:</strong>{" "}
              estratégias, sleeves, marcações de ativos, preferências de
              rebalanceamento, backtests executados e seus resultados.
            </li>
            <li>
              <strong className="text-ink">Apuração fiscal:</strong> DARFs e
              ajustes de custo que você registra para o IRPF.
            </li>
            <li>
              <strong className="text-ink">Assinatura:</strong> plano, situação da
              cobrança e o identificador do seu cadastro no provedor de pagamento.
              Dados de cartão nunca passam pelos nossos servidores.
            </li>
            <li>
              <strong className="text-ink">Registros técnicos:</strong> logs de
              acesso e erro do servidor, necessários para segurança e para o
              cumprimento do art. 15 do Marco Civil da Internet.
            </li>
          </ul>
          <p>
            Não pedimos CPF, não fazemos perfil comportamental e não tratamos
            dados sensíveis (art. 5º, II da LGPD).
          </p>
        </Section>

        <Section title="3. Para que usamos, e com que base legal">
          <ul className="list-disc space-y-1.5 pl-5">
            <li>
              Autenticar você e entregar carteira, IRPF, TLH, simulação e bench —{" "}
              <em>execução de contrato</em> (art. 7º, V).
            </li>
            <li>
              Cobrar a assinatura e emitir os registros contábeis correspondentes
              — <em>execução de contrato</em> e{" "}
              <em>cumprimento de obrigação legal</em> (art. 7º, II e V).
            </li>
            <li>
              Manter a segurança da plataforma, prevenir abuso e apurar
              incidentes — <em>legítimo interesse</em> (art. 7º, IX) e{" "}
              <em>obrigação legal</em> quanto a logs de acesso.
            </li>
          </ul>
          <p>
            Não usamos seus dados para publicidade, não os vendemos e não os
            compartilhamos com terceiros para fins comerciais.
          </p>
        </Section>

        <Section title="4. Com quem compartilhamos">
          <p>
            Apenas com operadores necessários para a plataforma funcionar, cada um
            tratando dados sob nossas instruções:
          </p>
          <ul className="list-disc space-y-1.5 pl-5">
            <li>
              <strong className="text-ink">Google</strong> — autenticação, quando
              você escolhe entrar com o Google.
            </li>
            <li>
              <strong className="text-ink">Stripe</strong> — cobrança de
              assinaturas; recebe seu email e o identificador da sua conta.
            </li>
            <li>
              <strong className="text-ink">Provedor de infraestrutura</strong> —
              hospedagem do servidor e do banco de dados.
            </li>
          </ul>
          <p>
            Também podemos compartilhar dados para cumprir ordem de autoridade
            competente ou exercer regularmente direitos (art. 7º, VI).
          </p>
        </Section>

        <Section title="5. Transferência internacional">
          <p>
            Google e Stripe processam dados fora do Brasil. A transferência se
            apoia no art. 33, II e IX da LGPD — cláusulas contratuais e a execução
            do contrato com você — e escolhemos fornecedores que assumem
            compromissos formais de proteção de dados.
          </p>
        </Section>

        <Section title="6. Por quanto tempo guardamos">
          <p>
            Enquanto sua conta existir, porque a apuração de IRPF e o custo médio
            das suas posições dependem do histórico completo de negociações. Ao
            excluir a conta, apagamos tudo imediatamente: extratos enviados,
            negociações, posições, backtests, estratégias e o próprio login.
          </p>
          <p>
            Duas exceções, previstas em lei: registros de acesso ficam retidos por
            até 6 meses (art. 15 do Marco Civil) e o provedor de pagamento mantém
            as notas fiscais pelo prazo fiscal, obrigação dele que a exclusão da
            conta não afasta.
          </p>
        </Section>

        <Section title="7. Cookies">
          <p>
            A plataforma não usa cookies, não tem analytics e não carrega
            rastreadores de terceiros. Seu token de sessão e a carteira que você
            está visualizando ficam no armazenamento local do próprio navegador —
            estritamente necessários para manter você conectado, e apagados quando
            você sai. Por isso não há banner de consentimento a exibir.
          </p>
        </Section>

        <Section title="8. Segurança">
          <p>
            Cada carteira é isolada por identificador de titular em todas as
            consultas; tráfego trafega por HTTPS; senhas, quando existem, são
            guardadas com hash; o acesso administrativo à infraestrutura é
            restrito. Em caso de incidente de segurança com risco relevante,
            comunicaremos você e a ANPD nos termos do art. 48.
          </p>
        </Section>

        <Section title="9. Seus direitos, e como exercê-los agora">
          <p>
            O art. 18 da LGPD garante confirmação do tratamento, acesso, correção,
            anonimização, portabilidade, eliminação, informação sobre
            compartilhamento e revogação do consentimento. Três deles estão na
            própria plataforma, sem prazo de espera, em{" "}
            <Link href="/conta" className="text-navy underline underline-offset-2">
              Meus dados
            </Link>
            :
          </p>
          <ul className="list-disc space-y-1.5 pl-5">
            <li>
              <strong className="text-ink">Confirmação e acesso</strong> — a
              página lista tudo o que guardamos, carteira por carteira.
            </li>
            <li>
              <strong className="text-ink">Portabilidade</strong> — baixe um
              arquivo JSON com todos os seus registros.
            </li>
            <li>
              <strong className="text-ink">Eliminação</strong> — exclua a conta e
              todos os dados, definitivamente.
            </li>
          </ul>
          <p>
            Para correção, oposição ou qualquer outro pedido, escreva para{" "}
            <Mail />; respondemos em até 15 dias.
          </p>
        </Section>

        <Section title="10. Decisões automatizadas">
          <p>
            Backtests, sugestões de rebalanceamento e planos de TLH são cálculos
            sobre os dados que você mesmo importou, apresentados como informação —
            não são recomendação de investimento, consultoria ou serviço de
            contabilidade, e nenhuma decisão automatizada nossa produz efeitos
            jurídicos sobre você. Ainda assim, se algum resultado o afetar, você
            pode pedir revisão pelo canal acima (art. 20).
          </p>
        </Section>

        <Section title="11. Reclamação à ANPD">
          <p>
            Se entender que uma solicitação não foi bem atendida, você pode
            reclamar à Autoridade Nacional de Proteção de Dados (ANPD), sem
            prejuízo das vias administrativa e judicial.
          </p>
        </Section>
      </main>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-3">
      <h2 className="font-display text-lg font-bold text-ink">{title}</h2>
      <div className="space-y-3 text-sm leading-relaxed text-muted">{children}</div>
    </section>
  );
}

function Mail() {
  return (
    <a
      href={`mailto:${CONTACT}`}
      className="text-navy underline underline-offset-2"
    >
      {CONTACT}
    </a>
  );
}
