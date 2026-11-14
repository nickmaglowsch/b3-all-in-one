// One page of an in-memory list, and the figures its pager prints.
//
// Every list the app holds client-side (positions, runs, imports, drift rows)
// used to render whole: 46 positions was a 2,000px table on a desktop and a
// 15,000px column of cards on a phone, with the page's next section somewhere
// below all of it. A page is the answer; this is the arithmetic, pure and
// import-free so `npm test` can run it through `node --test` directly.

export interface Page<T> {
  items: T[];
  /** 1-based, already clamped into range. */
  page: number;
  pages: number;
  /** 1-based index of the first row shown; 0 when the list is empty. */
  from: number;
  to: number;
  total: number;
}

/**
 * Rows `page` of `rows`, `size` at a time.
 *
 * The page is clamped rather than trusted: a filter that narrows 46 rows to 3
 * while the reader is on page 4 must land on the last page that exists, not on
 * an empty table that reads as "no matches".
 */
export function paginate<T>(rows: T[], page: number, size: number): Page<T> {
  const per = Math.max(1, Math.floor(size));
  const pages = Math.max(1, Math.ceil(rows.length / per));
  const p = Math.min(Math.max(1, Math.floor(page) || 1), pages);
  const start = (p - 1) * per;
  const items = rows.slice(start, start + per);
  return {
    items,
    page: p,
    pages,
    from: items.length ? start + 1 : 0,
    to: start + items.length,
    total: rows.length,
  };
}
