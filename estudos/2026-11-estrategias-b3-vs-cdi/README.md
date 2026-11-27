# Estratégias de ações da B3 contra o CDI (2012–2026, após IR)

**Pergunta:** das estratégias quantitativas que só compram ações da B3, quais renderam mais que o CDI depois do imposto de renda? E isso é melhor que escolher ao acaso?

O estudo teve duas rodadas:

- **v1** ([relatório](relatorios/relatorio_v1.pdf), [post no r/investimentos](https://www.reddit.com/r/investimentos/comments/1y4gtxd/)): ranking de 34 estratégias pelo índice de Sortino acima do CDI.
- **v2** ([relatório](relatorios/relatorio_v2.pdf)): refeito a partir das críticas dos comentários do post. Mede o retorno composto em vez de só o Sortino e acrescenta 75 carteiras sorteadas ("macacos"), testes com R$1 milhão e R$5 milhões e um mix de ETFs.

Os dados e o script desta pasta são os do **v2**.

## Resultado

| | |
|---|---|
| Estratégias que bateram o CDI composto nas duas metades (2012–18 e 2019–26) | **4 de 34**: Two-Leg Value, QDL-Equity, MomSharpe, SmallcapMom |
| Pelo critério do v1 (Sortino positivo nas duas metades) | 9 de 34 |
| Macacos que bateram o CDI | **0 de 75** |
| Estratégias com Sortino acima do melhor macaco | 13 de 34 |
| CDI após IR | 9,7% a.a. |
| BOVA11 parado (sem pagar IR) | 6,8% a.a. |
| Mix 1/3 DIVO11, 1/3 IVVB11, 1/3 CDI, rebalanceio semestral | **13,1% a.a.**, melhor que todas as 34 |
| Efeito de ir de R$100 mil para R$1 milhão | −0,4 a −0,9 p.p. ao ano nas de pouco giro; ~0 nas de momento |

Em uma frase: ganhar do macaco foi fácil, ganhar do CDI foi difícil, e o que mais rendeu foi diversificar para fora (quase todo o ganho do mix veio do IVVB11).

## Como foi medido

- **Janela:** jan/2012 a out/2026. Começa em 2012 para que as estratégias de fundamentos (dados da CVM desde 2010) concorram em pé de igualdade.
- **Simulação:** R$100 mil iniciais, grade mensal, parâmetros padrão de cada estratégia (nada ajustado para o estudo), slippage de 0,1% por operação.
- **Impostos:** motor de IR brasileiro. Ações pagam 15% sobre o ganho, com isenção para vendas de até R$20 mil no mês e compensação de prejuízo. ETFs pagam 15% sem isenção. Ganho não realizado não paga IR no valor final.
- **CDI de referência:** CDI menos 15% de IR, como um ETF de Tesouro Selic com taxa de administração.
- **Sortino:** média do excesso mensal sobre o CDI, dividida pelo desvio só dos meses abaixo do CDI, anualizada.
- **Excesso composto:** retorno anual composto da carteira menos o do CDI, no mesmo período. É a régua que importa para o bolso.
- **Metades:** 2012–2018 e 2019–out/2026, como cortes do mesmo backtest de 15 anos. No v1 eram backtests separados, por isso alguns números de metade mudaram um pouco.
- **Macacos:** a cada trimestre (jan/abr/jul/out), cada macaco sorteia 20 ações com peso igual. As famílias A e B sorteiam entre todas com volume ≥ R$1 milhão/dia; a família C, só no quartil mais líquido desse universo. São 25 sementes por família. O sorteio usa o retorno de um dia passado dividido pela volatilidade da própria ação. A primeira versão não dividia pela volatilidade, e isso viciava o sorteio em ações voláteis; ela foi descartada (está documentada no relatório v2).
- **Capital:** as 13 estratégias com Sortino positivo e o peso igual rodados também com R$1 milhão e R$5 milhões; o mix de ETFs, com R$1 milhão.

## O que tem aqui

```
estrategias/
  INDICE.md          as 34 estratégias, com tipo e link para a regra
  specs/             22 specs JSON, exatamente como rodaram
  controles/         macacos, carteiras de peso igual, CDI e ETFs (specs JSON)
dados/
  backtests.csv      os 145 backtests: estratégia, capital, parâmetros, retorno, queda máxima, IR pago, run_id
  curvas/            curva mensal de cada backtest (data, valor antes do IR, valor após IR)
    estrategias/     as 34, com R$100 mil
    capital_1M/      com R$1 milhão
    capital_5M/      com R$5 milhões
    macacos/         A01–A25, B01–B25, C01–C25
    controles/       CDI, peso igual, BOVA11, DIVO11, IVVB11, mix de ETFs
analise/
  recalcular.py      recalcula todos os números do relatório a partir das curvas
relatorios/
  relatorio_v1.pdf
  relatorio_v2.pdf
```

As 12 estratégias escritas em Python não estão copiadas aqui: o [índice](estrategias/INDICE.md) aponta para `engine/backtests/strategies/`. Os backtests rodaram com o código do commit `a5f0c2e`.

## Como conferir

```bash
python analise/recalcular.py
```

Sem dependências. O script imprime a tabela das 34 (Sortino total e por metade, retorno, excesso composto, testes v1 e v2), as estatísticas dos macacos, os controles e o efeito do capital. Os números devem bater com o relatório v2.

Para ir além da conta e testar as regras, veja os níveis 2 e 3 em [estudos/README.md](../README.md).

## Ressalvas

- **A vantagem é pequena.** As quatro robustas batem o CDI por 0,9 a 2,6 p.p. ao ano, com quedas de 35% a 56% no caminho.
- **Viés de seleção.** O macaco mede a sorte de escolher ações, não a sorte de escolher fatores. As 34 não são sorteios independentes: muitas usam momento, valor e qualidade, fatores que já se sabia que existiam. As estratégias AI H1–H5 foram desenhadas sobre dados que se sobrepõem a esta janela. Esse viés só diminui com dados que nenhuma estratégia viu.
- **Duas metades de ~7 anos é pouco.** Um teste fora da amostra em 2005–2011, só com estratégias de preço, ainda não foi feito.
- **Janeiro de 2012 fica de fora.** Todos os backtests montam a carteira no último pregão de janeiro de 2012, mês em que o Ibovespa subiu cerca de 11%. Vale igual para todas as carteiras, mas tira cerca de 0,7 p.p. ao ano de qualquer carteira 100% em ações.
- **Impacto de mercado não é simulado.** R$5 milhões em small caps não seriam executáveis aos preços do backtest.
- **Baixa volatilidade parece anômala.** LowVol, BottomVol e MaxEntropy ficaram perto de 0% a.a. A suspeita sobre os dados de preço ainda não foi investigada.
- **O mix de ETFs não é comparação de igual para igual.** As 34 só compram ações da B3; o mix aposta em dólar e em bolsa americana. Antes de out/2014 o terço do IVVB11 fica no CDI, porque o ETF ainda não existia.

Não é recomendação de investimento.
