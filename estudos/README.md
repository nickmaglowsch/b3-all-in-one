# Estudos

Pesquisas feitas com o bench (o motor de backtest deste repositório), publicadas de um jeito que qualquer um possa conferir.

Cada estudo fica numa pasta própria, com nome `AAAA-MM-tema`, e traz tudo o que é preciso para refazer as contas:

- **as regras** de cada estratégia testada: a spec JSON exatamente como rodou, ou o link para o código em `engine/`;
- **os dados de saída**: a curva mensal de cada backtest (valor antes e depois do IR) e uma tabela com todos os runs;
- **um script de análise** que recalcula os números do relatório a partir dessas curvas, usando só a biblioteca padrão do Python;
- **o relatório** em PDF.

## Como conferir um estudo

Há três níveis de contraprova, do mais barato ao mais completo:

1. **Refazer as contas.** Rode `python analise/recalcular.py` dentro da pasta do estudo. Ele lê as curvas em `dados/curvas/` e recalcula Sortino, retornos e testes. Assim você confere que os números do relatório saem dos backtests, sem precisar de nenhum dado de mercado.
2. **Reimplementar as regras com os seus dados.** As regras estão em `estrategias/`. Os dados de origem são públicos: preços da B3 (COTAHIST), demonstrações da CVM (DFP, ITR, FRE) e CDI do Banco Central (SGS, série 12). Se uma implementação independente der muito diferente, alguém errou, e queremos saber quem.
3. **Rodar no motor.** O código do motor, do simulador e do cálculo de IR está neste repositório (`engine/` e `taxcore/`). As specs JSON podem ser carregadas como estratégias, inclusive pelo servidor MCP do bench (veja o README principal).

## Estudos

| Pasta | Pergunta | Data |
|---|---|---|
| [2026-11-estrategias-b3-vs-cdi](2026-11-estrategias-b3-vs-cdi/) | Quais estratégias que só compram ações da B3 bateram o CDI depois do IR em 2012–2026? E contra carteiras sorteadas, com mais capital e contra um mix de ETFs? | nov/2026 |

Nada aqui é recomendação de investimento. É desempenho passado, simulado.
