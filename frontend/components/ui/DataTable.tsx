import type { ReactNode } from "react";

export interface Column<T> {
  key: string;
  header: ReactNode;
  align?: "left" | "right";
  render: (row: T) => ReactNode;
  /** extra classes on the cell (e.g. width) */
  className?: string;
}

// Dense, hairline-ruled, right-aligned numerics. No zebra — rules do the work.
export function DataTable<T>({
  columns,
  rows,
  getRowKey,
}: {
  columns: Column<T>[];
  rows: T[];
  getRowKey: (row: T, index: number) => string | number;
}) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-sm">
        <thead>
          <tr className="border-b border-hairline">
            {columns.map((c) => (
              <th
                key={c.key}
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
          {rows.map((row, i) => (
            <tr
              key={getRowKey(row, i)}
              className="border-b border-hairline/70 last:border-0"
            >
              {columns.map((c) => (
                <td
                  key={c.key}
                  className={`px-3 py-2 align-middle text-ink ${
                    c.align === "right"
                      ? "text-right font-mono tnum tabular-nums"
                      : "text-left"
                  } ${c.className ?? ""}`}
                >
                  {c.render(row)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
