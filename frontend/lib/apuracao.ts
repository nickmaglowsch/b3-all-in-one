// The one worked example every landing variation shows, and the arithmetic that
// closes it. Shared so five pages cannot drift into five different DARFs.
// Synthetic ("exemplo") figures — the product is pre-launch.

export const VENDAS = 24310; // 4 vendas de julho, dos extratos
export const CUSTO = 22028.8; // preço médio, ajustado por eventos
export const GANHO = round2(VENDAS - CUSTO);
export const PREJUIZO = 640; // saldo da mesma classe
export const BASE = round2(GANHO - PREJUIZO);
export const ALIQUOTA = 0.15; // ações, operação comum
export const DARF = round2(BASE * ALIQUOTA);

// Isenção mensal de vendas de ações (swing trade).
export const ISENCAO = 20000;

function round2(n: number) {
  return Math.round(n * 100) / 100;
}

// Imposto de um mês de ações em operação comum: a isenção olha o total vendido,
// não o ganho. Prejuízo acumulado abate a base antes da alíquota.
export function impostoMes(vendas: number, ganho: number, prejuizo = 0) {
  if (vendas <= ISENCAO) return 0;
  return round2(Math.max(0, ganho - prejuizo) * ALIQUOTA);
}

// A curva depois do imposto. A alíquota sai de cada período com ganho, não do
// resultado final — e um período negativo vira prejuízo a compensar nos
// seguintes, que é a mesma regra da apuração mensal aplicada a uma série. A
// diferença é visível justamente na recuperação de uma queda: ali o ganho volta
// sem imposto até o prejuízo acumulado zerar.
//
// Descontar 15% do ganho acumulado no fim (o atalho óbvio) dá outro número e
// contradiz a frase que a página usa para se diferenciar.
export function serieLiquida(bruto: number[], aliquota = ALIQUOTA) {
  const out = [bruto[0]];
  let prejuizo = 0; // saldo a compensar, sempre positivo
  for (let i = 1; i < bruto.length; i++) {
    const anterior = out[i - 1];
    const ganho = anterior * (bruto[i] / bruto[i - 1] - 1);
    if (ganho <= 0) {
      prejuizo -= ganho;
      out.push(anterior + ganho);
      continue;
    }
    const tributavel = Math.max(0, ganho - prejuizo);
    prejuizo = Math.max(0, prejuizo - ganho);
    out.push(anterior + ganho - tributavel * aliquota);
  }
  return out;
}

// Polyline through a series, normalized into a 100×40 viewBox with padding.
// Shared by the variations' sparklines; `lo`/`hi` let two series share a scale.
export function linePath(values: number[], lo?: number, hi?: number) {
  const min = lo ?? Math.min(...values);
  const max = hi ?? Math.max(...values);
  const rng = max - min || 1;
  const W = 100,
    H = 40,
    P = 4;
  const step = (W - P * 2) / (values.length - 1);
  return values
    .map((v, i) => {
      const x = P + i * step;
      const y = H - P - ((v - min) / rng) * (H - P * 2);
      return `${i ? "L" : "M"}${x.toFixed(1)} ${y.toFixed(1)}`;
    })
    .join(" ");
}
