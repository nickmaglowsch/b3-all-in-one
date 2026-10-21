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

  # Ações, BDRs e ETFs de renda variável são todos "operações comuns" (IN RFB
  # 1.585/2015, art. 64): um só saldo de prejuízo. Só a isenção de R$ 20.000 é
  # exclusiva das ações (Solução de Consulta Cosit 145/2021 e 39/2022).
  Cenário: Prejuízo de BDR abate o lucro do mês seguinte em BDR
    Dado um mês com vendas de "BDR" de R$ 10000.00 e lucro de R$ -10000.00
    E o mês seguinte com vendas de "BDR" de R$ 10000.00 e lucro de R$ 10000.00
    Quando eu apuro o mês seguinte
    Então o IR devido é R$ 0.00
    E o prejuízo acumulado ao fim do mês é R$ 0.00

  Cenário: Prejuízo de ETF abate o lucro tributável de ações
    Dado um mês com vendas de "ETF_RV" de R$ 30000.00 e lucro de R$ -4000.00
    E o mês seguinte com vendas de "AÇÃO" de R$ 30000.00 e lucro de R$ 10000.00
    Quando eu apuro o mês seguinte
    Então o IR devido é R$ 900.00
    E o prejuízo acumulado ao fim do mês é R$ 0.00

  Cenário: ETF não tem isenção, mesmo com vendas pequenas
    Dado um mês com vendas de "ETF_RV" de R$ 5000.00 e lucro de R$ 1000.00
    Quando eu apuro o mês
    Então o mês não é isento
    E o IR devido é R$ 150.00

  # Day trade: compra e venda do mesmo ativo no mesmo dia, na mesma corretora
  # (Lei 9.959/2000, art. 8º). 20%, sem isenção, prejuízo só contra day trade.
  Cenário: Day trade é tributado a 20% mesmo abaixo de R$ 20.000
    Dado um mês com vendas de "DT" de R$ 3100.00 e lucro de R$ 100.00
    Quando eu apuro o mês
    Então o mês não é isento
    E o IR devido é R$ 20.00

  Cenário: Prejuízo de ações não abate lucro de day trade
    Dado um mês com vendas de "AÇÃO" de R$ 30000.00 e lucro de R$ -5000.00
    E o mês seguinte com vendas de "DT" de R$ 10000.00 e lucro de R$ 1000.00
    Quando eu apuro o mês seguinte
    Então o IR devido é R$ 200.00
    E o prejuízo acumulado ao fim do mês é R$ 0.00
