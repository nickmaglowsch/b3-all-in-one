# language: pt
Funcionalidade: Apuração mensal de IR sobre vendas na B3
  As regras que a apuração tem de obedecer, escritas para serem conferidas por
  quem entende de imposto e não de código. Cada cenário aqui é executável —
  tests/test_apuracao_bdd.py liga cada passo a scripts.apuracao.monthly_apuracao.

  Cenário: Vendas de ações até R$ 20.000 no mês são isentas
    Dado um mês com vendas de "AÇÃO" de R$ 20000.00 e lucro de R$ 5000.00
    Quando eu apuro o mês
    Então o mês é isento
    E o IR devido é R$ 0.00
    E o ganho isento é R$ 5000.00

  Cenário: Um centavo acima do limite tributa o lucro inteiro a 15%
    Dado um mês com vendas de "AÇÃO" de R$ 20000.01 e lucro de R$ 5000.00
    Quando eu apuro o mês
    Então o mês não é isento
    E o IR devido é R$ 750.00

  Cenário: FII não tem isenção — qualquer lucro é tributado a 20%
    Dado um mês com vendas de "FII" de R$ 1000.00 e lucro de R$ 1000.00
    Quando eu apuro o mês
    Então o mês não é isento
    E o IR devido é R$ 200.00

  Cenário: Prejuízo de um mês abate o lucro do mês seguinte
    Dado um mês com vendas de "FII" de R$ 30000.00 e lucro de R$ -1000.00
    E o mês seguinte com vendas de "FII" de R$ 30000.00 e lucro de R$ 3000.00
    Quando eu apuro o mês seguinte
    Então o IR devido é R$ 400.00

  # INTERPRETAÇÃO, não regra pacífica. Este é o "melhor dos dois mundos" que o
  # projeto adota (ver CLAUDE.md): lucro isento não consome o prejuízo acumulado,
  # mas prejuízo em mês isento continua alimentando-o. Há leitura da RFB (Perguntas
  # e Respostas IRPF, ganhos líquidos em renda variável) de que perdas em operações
  # isentas não são compensáveis — sob ela, o mês seguinte deste cenário deveria
  # R$ 750,00, não R$ 300,00. Confirme com seu contador antes de entregar.
  Cenário: Prejuízo em mês isento ainda abate o lucro futuro
    Dado um mês com vendas de "AÇÃO" de R$ 10000.00 e lucro de R$ -1000.00
    E o mês seguinte com vendas de "AÇÃO" de R$ 30000.00 e lucro de R$ 3000.00
    Quando eu apuro o mês seguinte
    Então o IR devido é R$ 300.00

  Cenário: Lucro em mês isento não consome prejuízo acumulado
    Dado um mês com vendas de "AÇÃO" de R$ 30000.00 e lucro de R$ -1000.00
    E o mês seguinte com vendas de "AÇÃO" de R$ 10000.00 e lucro de R$ 1000.00
    Quando eu apuro o mês seguinte
    Então o IR devido é R$ 0.00
    E o prejuízo acumulado ao fim do mês é R$ 1000.00

  # LIMITAÇÃO conhecida, documentada aqui para não ser confundida com regra: BDR e
  # ETF de renda variável não têm bucket de prejuízo neste relatório. O prejuízo de
  # um mês não abate o lucro do mês seguinte, e não se cruza com o de ações — mesmo
  # sendo todas operações comuns a 15%, compensáveis entre si na leitura usual. O
  # relatório portanto recolhe a mais nesses casos. Comportamento antigo, mantido de
  # propósito: corrigi-lo muda valores que o usuário já pode ter recolhido.
  Cenário: BDR e ETF não acumulam prejuízo — o mês seguinte paga integral
    Dado um mês com vendas de "BDR" de R$ 10000.00 e lucro de R$ -10000.00
    E o mês seguinte com vendas de "BDR" de R$ 10000.00 e lucro de R$ 10000.00
    Quando eu apuro o mês seguinte
    Então o IR devido é R$ 1500.00
    E o prejuízo acumulado ao fim do mês é R$ 0.00
