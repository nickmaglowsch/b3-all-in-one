"use client";

/**
 * Sorting for the card layout — phones only (`md:hidden`).
 *
 * Below `md` a table's rows are stacked cards and the `<thead>` is gone, so the
 * column headers that carried the sort are gone with it. Without this, the sort
 * a phone gets is whatever the page defaulted to, forever. The control is a
 * native `<select>` plus a direction toggle, which is what the platform's own
 * lists use and what its pickers, keyboard and screen reader already know.
 *
 * The desktop headers stay exactly as they were; this drives the same state.
 */
export function SortControl<K extends string>({
  options,
  value,
  dir,
  onPick,
  onToggleDir,
  label = "Ordenar por",
}: {
  options: { key: K; label: string }[];
  value: K;
  dir: "asc" | "desc";
  /** Picking a field; the caller decides that field's natural first direction. */
  onPick: (key: K) => void;
  onToggleDir: () => void;
  label?: string;
}) {
  const current = options.find((o) => o.key === value)?.label ?? value;

  return (
    <div className="flex items-center gap-2 md:hidden">
      <label className="flex min-w-0 items-center gap-2 text-xs text-muted">
        <span className="sr-only">{label}</span>
        <select
          value={value}
          onChange={(e) => onPick(e.target.value as K)}
          aria-label={label}
          className="min-w-0 max-w-[9.5rem] truncate rounded-md border border-hairline bg-paper px-2 py-1.5 text-xs text-ink focus:border-navy focus:outline-none"
        >
          {options.map((o) => (
            <option key={o.key} value={o.key}>
              {o.label}
            </option>
          ))}
        </select>
      </label>
      <button
        type="button"
        onClick={onToggleDir}
        // The arrow shows the order now; the name says what the tap will do.
        // Saying both in one string is how a toggle ends up ambiguous.
        aria-label={`Ordenar ${current} em ordem ${dir === "asc" ? "decrescente" : "crescente"}`}
        className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md border border-hairline text-muted active:bg-hairline/60"
      >
        <span aria-hidden className="text-xs leading-none">
          {dir === "asc" ? "▲" : "▼"}
        </span>
      </button>
    </div>
  );
}
