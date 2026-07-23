// pt-BR formatters. Money in BRL, dates dd/mm/aaaa, percentages from fractions.

const brl = new Intl.NumberFormat("pt-BR", {
  style: "currency",
  currency: "BRL",
});

export function formatMoney(value: number, withSign = false): string {
  const s = brl.format(value);
  // Intl already prefixes "-" for negatives; add explicit "+" when asked.
  return withSign && value > 0 ? `+${s}` : s;
}

const pct = new Intl.NumberFormat("pt-BR", {
  style: "percent",
  minimumFractionDigits: 1,
  maximumFractionDigits: 2,
});

// Input is a fraction: 0.0123 → "1,23%".
export function formatPercent(fraction: number): string {
  return pct.format(fraction);
}

const num = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 8 });
export function formatQty(value: number): string {
  return num.format(value);
}

const int = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 0 });
export function formatInt(value: number): string {
  return int.format(value);
}

// ISO date (yyyy-mm-dd or full ISO) → dd/mm/aaaa. Parses as calendar date to
// avoid timezone drift on date-only strings.
export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const datePart = iso.slice(0, 10);
  const [y, m, d] = datePart.split("-");
  if (y && m && d) return `${d}/${m}/${y}`;
  return iso;
}
