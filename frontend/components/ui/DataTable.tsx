import type { ReactNode } from "react";

/** How one column behaves once the table becomes a stack of cards (below `md`).
 *
 *  - `title` — the card's headline: full width, no label, first in the card.
 *    The ticker, the filename, the month. One per table.
 *  - `full`  — a row of its own, left-aligned and unlabelled. For a cell that is
 *    a control rather than a datum.
 *  - `off`   — dropped. A sparkline whose meaning lived in its column header, or
 *    a value the card already states another way.
 *  - default — a labelled line: the column name on the left, the value right. */
export type MobileRole = "title" | "full" | "off";

const MOBILE_CLASS: Record<MobileRole, string> = {
  title: "rt-title",
  full: "rt-full",
  off: "rt-off",
};

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
  /**
   * Plain-text column name, carried into each cell as its label in the card
   * layout. Required whenever `header` is not a bare string — a sort button, an
   * icon — because a phone shows no `<thead>` to read the name off.
   */
  label?: string;
  /** Role in the card layout. Omit for a labelled value line. */
  mobile?: MobileRole;
}

// Dense, hairline-ruled, right-aligned numerics on a wide screen. No zebra —
// rules do the work. Below `md` the same rows are stacked cards; see the
// `.rtable` block in globals.css, which is where that layout lives.
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
  const keyed = rows.map((row, i) => ({ row, key: getRowKey(row, i) }));
  const foot =
    footRowKey === undefined ? undefined : keyed.find((r) => r.key === footRowKey);
  const body = keyed.filter((r) => r !== foot);

  // A column whose header is already plain text needs no second spelling of it.
  const nameOf = (c: Column<T>) =>
    c.label ?? (typeof c.header === "string" ? c.header : undefined);

  const cells = (row: T, key: string | number, isFoot: boolean) => (
    <tr
      key={key}
      className={
        isFoot
          ? // The number people came for stops being one row among many.
            "border-t-2 border-hairline"
          : "border-b border-hairline/70 last:border-0"
      }
    >
      {columns.map((c) => {
        const name = nameOf(c);
        return (
          <td
            key={c.key}
            className={`px-3 py-2 align-middle text-ink ${
              c.align === "right" ? "text-right" : "text-left"
            } ${
              (c.numeric ?? c.align === "right") ? "font-mono tnum tabular-nums" : ""
            } ${c.mobile ? MOBILE_CLASS[c.mobile] : ""} ${c.className ?? ""}`}
          >
            {/* Real text, not a `::before`: the card layout hides the column
                headers, so this is the only place the column's name exists for
                anyone reading the row aloud. Display:none above `md`. */}
            {name && !c.mobile && <span className="rt-label">{name}</span>}
            <span className="rt-value">{c.render(row)}</span>
          </td>
        );
      })}
    </tr>
  );

  return (
    // Below `md` nothing here overflows — the rows are cards. Above it, a wide
    // table scrolls horizontally, and a scroll container that nothing can focus
    // is unreachable by keyboard: the right-hand columns simply do not exist for
    // anyone not using a mouse. tabIndex makes it a focusable region; the name
    // tells a screen reader what it just entered.
    // role="region" ONLY when there is a name for it. An unnamed region is an
    // unnamed landmark, and pairing it with tabIndex would add a tab stop that
    // announces nothing — worse than the plain scroll container it replaced, on
    // every call site that has not passed a label yet.
    <div
      className="overflow-x-auto focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-navy"
      tabIndex={0}
      role={label ? "region" : undefined}
      aria-label={label}
    >
      <table className="rtable w-full border-collapse text-sm">
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
          {body.map(({ row, key }) => cells(row, key, false))}
        </tbody>
        {/* A real <tfoot>, not just a heavier rule: the totals row is a summary
            of the rows above it, and the element says so to anyone not reading
            the border. Rendered after tbody in the DOM, which is where the spec
            has wanted it since HTML5. */}
        {foot && (
          <tfoot>{cells(foot.row, foot.key, true)}</tfoot>
        )}
      </table>
    </div>
  );
}
