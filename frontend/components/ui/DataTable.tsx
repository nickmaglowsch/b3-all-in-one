import type { ReactNode } from "react";

export interface Column<T> {
  key: string;
  header: ReactNode;
  align?: "left" | "right";
  render: (row: T) => ReactNode;
  /** extra classes on the cell (e.g. width) */
  className?: string;
  /**
   * Tabular figures. Defaults to `align === "right"`, which covers most numerics,
   * but a left-aligned date or year column is still a number that stacks — set it
   * explicitly there. Right-aligned text can opt out with `false`.
   */
  numeric?: boolean;
  /** Current sort state for this column, announced via `aria-sort`. */
  sort?: "asc" | "desc";
  /** True when the column is sortable but not the active sort. */
  sortable?: boolean;
}

// Dense, hairline-ruled, right-aligned numerics. No zebra — rules do the work.
export function DataTable<T>({
  columns,
  rows,
  getRowKey,
  label,
  footRowKey,
}: {
  columns: Column<T>[];
  rows: T[];
  getRowKey: (row: T, index: number) => string | number;
  /** Accessible name for the scroll region and the table itself. */
  label?: string;
  /** Row key that is a summary/total, ruled off from the body above it. */
  footRowKey?: string | number;
}) {
  return (
    // Wide tables scroll horizontally, and a scroll container that nothing can
    // focus is unreachable by keyboard — the right-hand columns simply do not
    // exist for anyone not using a mouse. tabIndex makes it a focusable region;
    // the name tells a screen reader what it just entered.
    <div
      className="overflow-x-auto focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-navy"
      tabIndex={0}
      role="region"
      aria-label={label}
    >
      <table className="w-full border-collapse text-sm">
        <thead>
          <tr className="border-b border-hairline">
            {columns.map((c) => (
              <th
                key={c.key}
                scope="col"
                aria-sort={
                  c.sort ? (c.sort === "asc" ? "ascending" : "descending") : c.sortable ? "none" : undefined
                }
                className={`px-3 py-2 text-[11px] font-semibold uppercase tracking-wide text-muted ${
                  c.align === "right" ? "text-right" : "text-left"
                } ${c.className ?? ""}`}
              >
                {c.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => {
            const key = getRowKey(row, i);
            const isFoot = footRowKey !== undefined && key === footRowKey;
            return (
              <tr
                key={key}
                className={
                  isFoot
                    ? // The number people came for stops being one row among many.
                      "border-t-2 border-hairline"
                    : "border-b border-hairline/70 last:border-0"
                }
              >
                {columns.map((c) => (
                  <td
                    key={c.key}
                    className={`px-3 py-2 align-middle text-ink ${
                      c.align === "right" ? "text-right" : "text-left"
                    } ${
                      (c.numeric ?? c.align === "right")
                        ? "font-mono tnum tabular-nums"
                        : ""
                    } ${c.className ?? ""}`}
                  >
                    {c.render(row)}
                  </td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
