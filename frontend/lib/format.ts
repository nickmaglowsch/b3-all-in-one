// pt-BR formatters. Money in BRL, dates dd/mm/aaaa, percentages from fractions.

const brl = new Intl.NumberFormat("pt-BR", {
  style: "currency",
  currency: "BRL",
});

// The value as the screen prints it. Below the last digit shown it is float dust
// — a P&L of mark − cost with mark == cost computes as −1e-10 — and dust is zero:
// not "-R$ 0,00" in red, nor "▼ -0,0%". Colour and glyph decisions go through
// these too, so they always agree with the digits next to them.
export const moneyShown = (v: number) => (Math.abs(v) < 0.005 ? 0 : v);
export const fractionShown = (f: number) => (Math.abs(f) < 0.00005 ? 0 : f);

export function formatMoney(value: number, withSign = false): string {
  const v = moneyShown(value);
  const s = brl.format(v);
  // Intl already prefixes "-" for negatives; add explicit "+" when asked.
  return withSign && v > 0 ? `+${s}` : s;
}

const pct = new Intl.NumberFormat("pt-BR", {
  style: "percent",
  minimumFractionDigits: 1,
  maximumFractionDigits: 2,
});

// Input is a fraction: 0.0123 → "1,23%".
export function formatPercent(fraction: number): string {
  return pct.format(fractionShown(fraction));
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

// The São Paulo calendar day of a *timestamp* — formatDate's counterpart for an
// instant. A row saved at 22:30 BRT is stored as 01:30Z the next day, and
// slicing that string files it under tomorrow. A bare date passes through
// formatDate untouched: it has no instant to convert.
const dateSP = new Intl.DateTimeFormat("pt-BR", {
  timeZone: "America/Sao_Paulo",
  day: "2-digit",
  month: "2-digit",
  year: "numeric",
});
export function formatInstantDate(iso: string | null | undefined): string {
  if (!iso || iso.length <= 10) return formatDate(iso);
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return dateSP.format(d);
}

// Engine metric %-number (8.9 → "8,9%"), not a fraction. Signed prefixes an
// explicit +; a missing metric reads "—".
export function formatMetricPct(n: number | null, signed = false): string {
  if (n == null) return "—";
  const s = n.toLocaleString("pt-BR", { minimumFractionDigits: 1, maximumFractionDigits: 2 });
  return `${signed && n > 0 ? "+" : ""}${s}%`;
}

export const formatRatio = (n: number | null) =>
  n == null ? "—" : n.toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

// Compact BRL for chart axes / bar labels (NAV runs into 6-7 figures).
export function compactBRL(v: number): string {
  const a = Math.abs(v);
  const sign = v < 0 ? "-" : "";
  if (a >= 1e6) return `${sign}R$ ${(a / 1e6).toLocaleString("pt-BR", { maximumFractionDigits: 1 })} mi`;
  if (a >= 1e3) return `${sign}R$ ${(a / 1e3).toLocaleString("pt-BR", { maximumFractionDigits: 0 })} mil`;
  return `${sign}R$ ${a.toLocaleString("pt-BR", { maximumFractionDigits: 0 })}`;
}

// Rebalance frequencies: the engine's freq codes, mensal / trimestral / semanal.
export const REBALANCE_FREQS = [
  { value: "ME", label: "Mensal" },
  { value: "QE", label: "Trimestral" },
  { value: "W-FRI", label: "Semanal" },
] as const;
