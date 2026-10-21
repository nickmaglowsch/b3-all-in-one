// Os anos que as telas do IRPF oferecem, tirados do relógio — não de uma lista
// escrita à mão, que envelhece um ano a cada janeiro e, enquanto isso, esconde
// justamente o ano cujos DARFs estão vencendo.
//
// Puro e sem imports, porque `npm test` roda este arquivo direto no `node --test`.

const SP_YEAR = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/Sao_Paulo",
  year: "numeric",
});

/** O ano corrente em São Paulo — o calendário da Receita, não o do UTC, que vira
 *  o ano às 21h do dia 31. */
export function anoCorrente(now: Date = new Date()): number {
  return Number(SP_YEAR.format(now));
}

/** `n` anos, do corrente para trás. O corrente entra: é nele que os DARFs do mês
 *  vencem, e o relatório dele é a parcial do ano. */
export function anosRecentes(atual: number, n = 6): number[] {
  return Array.from({ length: n }, (_, i) => atual - i);
}

/** O ano-base que se declara agora: o anterior ao corrente. */
export function anoBasePadrao(atual: number): number {
  return atual - 1;
}
