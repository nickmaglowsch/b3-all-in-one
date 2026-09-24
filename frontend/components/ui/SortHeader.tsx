"use client";

/**
 * Sortable desktop column header — the `<th>` content of a DataTable column
 * that sorts. SortControl is its phone counterpart; both drive the same state.
 */
export type Dir = "asc" | "desc";

export function SortHeader({
  label,
  active,
  dir,
  fallback,
  align,
  onClick,
}: {
  label: string;
  active: boolean;
  dir: Dir;
  /** Direction a first click on this column sorts by, while it is inactive. */
  fallback: Dir;
  align: "left" | "right";
  onClick: () => void;
}) {
  // The label names what the click will DO; the current state is on the <th> as
  // aria-sort, which is where a screen reader expects it. Saying both in one
  // string is how "Ativo (crescente)" ends up ambiguous about whether it
  // describes the order now or the order after clicking.
  const nextDir = active ? (dir === "asc" ? "desc" : "asc") : fallback;
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={`Ordenar por ${label}, ${nextDir === "asc" ? "crescente" : "decrescente"}`}
      className={`inline-flex items-center gap-1 uppercase tracking-wide transition-colors hover:text-ink ${
        active ? "text-ink" : ""
      } ${align === "right" ? "flex-row-reverse" : ""}`}
    >
      {label}
      <span aria-hidden className="text-[0.85em] leading-none">
        {active ? (dir === "asc" ? "▲" : "▼") : "↕"}
      </span>
    </button>
  );
}
