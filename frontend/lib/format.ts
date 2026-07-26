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

// dd/mm/aaaa às HH:MM in America/Sao_Paulo. `formatDate` slices the ISO string,
// which is right for a plain date but wrong for a timestamp: it drops the time
// entirely and, on a UTC value, shifts the day across the -03:00 boundary (a run
// at 01:00Z would read as the next day). Used where "when was this computed"
// carries real meaning — a month-to-date tax figure, for one.
const dateTime = new Intl.DateTimeFormat("pt-BR", {
  timeZone: "America/Sao_Paulo",
  day: "2-digit",
  month: "2-digit",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});
export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return dateTime.format(d).replace(", ", " às ");
}
