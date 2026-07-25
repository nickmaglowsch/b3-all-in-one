# Privacidade e LGPD

How the platform complies with the **Lei Geral de Proteção de Dados** (Lei 13.709/2018),
and where each obligation lives in the code. This doubles as the *registro das operações
de tratamento* the ANPD can ask a controller for (art. 37) — keep it current when a table,
an operator or a purpose changes.

Risk #2 in `00-overview.md` ("financial-data custody / LGPD") is closed by this document
plus `platform_app/privacy.py`.

## Who is who

| Papel | Quem | Quando |
|---|---|---|
| Titular | The investor whose B3 extracts these are | Always |
| Controlador | Quantax | For accounts managing their own book |
| Operador | Quantax | For the client books an `enterprise` account manages — there the escritório is the controller and we act on its instructions |
| Suboperadores | Google (login), Stripe (cobrança), the VPS/hosting provider | See below |

The enterprise split matters: a firm's client never signed up here, so we have no direct
relationship with them. `DELETE /privacy/account` therefore erases the firm's managed books
too — an operator that kept them after the controller left would be processing without a
basis.

## Dados tratados, finalidade e base legal

| Dado | Onde vive | Finalidade | Base legal (art. 7º) |
|---|---|---|---|
| Email, senha (hash), id/email Google | `user`, `oauth_account` | Autenticar | V — execução de contrato |
| Extratos `.xlsx` da B3 enviados | `UPLOADS_DIR/{tenant_id}/`, `imports` | Reconstruir a carteira e apurar imposto | V |
| Negociações, eventos de caixa, posições | `trades`, `cash_events`, `positions` | Carteira, IRPF, TLH, índice | V |
| Ajustes de custo, DARFs | `cost_basis_overrides`, `darf_payments` | Apuração fiscal do titular | V |
| Estratégias, sleeves, marcações, preferências | `user_strategies`, `portfolio_sleeves`, `asset_class_overrides`, `rebalance_settings` | Entregar a função contratada | V |
| Backtests e artefatos | `backtest_runs`, `ARTIFACTS_DIR/{run_id}/` | Entregar a função contratada | V |
| Rótulo das carteiras de clientes | `portfolios` | Organizar as carteiras do escritório | V |
| Plano, situação da cobrança, `stripe_customer_id` | `user` | Cobrar e manter registro contábil | II e V |
| Logs de acesso do servidor | Infra (fora do Postgres) | Segurança, apuração de incidente | IX + art. 15 do Marco Civil |

Não tratamos dados sensíveis (art. 5º, II), não pedimos CPF, não fazemos perfilamento
comportamental e não há publicidade nem venda de dados.

**Cookies:** nenhum. A sessão é um Bearer token em `localStorage` e a carteira selecionada
fica em `sessionStorage` — armazenamento estritamente necessário, dispensando consentimento
e banner. Um analytics futuro muda isso e exige consentimento prévio.

## Direitos do titular (art. 18), servidos pelo produto

| Direito | Como | Rota |
|---|---|---|
| I/II — confirmação e acesso | Página **Meus dados**, contagem por carteira | `GET /privacy/summary` |
| V — portabilidade | JSON com todas as linhas de todas as carteiras | `GET /privacy/export` |
| VI — eliminação | Apaga linhas, arquivos e o próprio login | `DELETE /privacy/account` |
| III, IV, VII, IX — correção, anonimização, informação, oposição | Canal do encarregado | `privacidade@quantax.com.br` |

Os três primeiros são imediatos e não passam por atendimento humano, então o prazo de 15
dias do art. 19 nunca corre para eles.

**Totalidade por construção.** `privacy.py` e `routes_portfolios.purge_tenant_rows`
descobrem as tabelas em `Base.metadata` filtrando pela coluna `tenant_id`, em vez de manter
uma lista à mão. Uma tabela nova entra no export e na exclusão sozinha; `tests/test_privacy.py`
semeia *todas* elas e falha se alguma escapar.

**A exclusão é definitiva**, não uma lápide anonimizada: nada no schema mantém FK não-nula
para o login depois que as carteiras saem, e os registros que um controlador precisa reter
para fins fiscais ficam no Stripe, o operador, não aqui. A conta também pede ao Stripe que
apague o cadastro do cliente — o que cancela a assinatura, para que uma conta excluída não
continue sendo cobrada. Falha do Stripe não bloqueia a exclusão: o direito não pode ficar
refém de terceiro.

## Retenção

Enquanto a conta existir — o custo médio e a apuração de IRPF dependem do histórico
completo, então um expurgo por idade quebraria o produto e prejudicaria o titular.
Exclusão da conta apaga tudo na hora. As exceções são de terceiros: logs de acesso por até
6 meses (art. 15 do Marco Civil) e notas fiscais retidas pelo Stripe pelo prazo fiscal.

## Transferência internacional

Google e Stripe processam fora do Brasil, com base no art. 33, II (cláusulas contratuais) e
IX (execução de contrato com o titular).

## Segurança (art. 46)

- Isolamento por `tenant_id` em toda consulta; `tenancy.get_current_tenant` é a única porta
  que escolhe um `tenant_id`, e checa posse antes de devolvê-lo.
- Senhas com hash (fastapi-users); tokens JWT de vida curta (1h).
- Segredos por variável de ambiente, fora do repositório.
- HTTPS na borda; CORS restrito a `ALLOWED_ORIGINS`.
- O export nunca inclui caminhos do servidor (`stored_path`, `artifacts_path`) nem o
  `stripe_customer_id` — só a informação de que ele existe.

Pendências conhecidas, na ordem em que valem a pena: criptografia em repouso no volume do
Postgres, RLS no banco (o gancho está documentado em `tenancy.get_current_tenant`) e
retenção explícita de logs na infra.

## Incidente de segurança (art. 48)

Havendo risco relevante ao titular: comunicar ANPD e titulares afetados em prazo razoável,
descrevendo os dados atingidos, os titulares envolvidos, as medidas técnicas usadas e o que
foi feito para reverter ou mitigar o dano.

## Onde está no código

- `platform/platform_app/privacy.py` — as três rotas do art. 18.
- `platform/platform_app/routes_portfolios.py` — `purge_tenant_rows` / `purge_tenant_files`,
  o expurgo compartilhado entre offboarding de cliente e exclusão de conta.
- `platform/tests/test_privacy.py` — os direitos como testes.
- `frontend/app/privacidade/page.tsx` — a política, pública (legível antes do cadastro).
- `frontend/app/conta/page.tsx` — a página onde o titular exerce os direitos.
