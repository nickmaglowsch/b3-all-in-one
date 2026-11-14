"use client";

import { useRef, useState, useSyncExternalStore } from "react";
import { paginate } from "@/lib/paginate";
import { formatInt } from "@/lib/format";

/**
 * Page `rows` in memory. `resetKey` sends the reader back to page 1 when it
 * changes — a new filter or sort is a new list, and staying on page 3 of it
 * shows rows nobody asked for. (A list that merely shrinks is clamped by
 * `paginate` instead.)
 */
export function usePaged<T>(rows: T[], size: number, resetKey: string = "") {
  const [page, setPage] = useState(1);
  const [lastKey, setLastKey] = useState(resetKey);
  if (lastKey !== resetKey) {
    setLastKey(resetKey);
    setPage(1);
  }
  return { ...paginate(rows, page, size), setPage };
}

const WIDE = "(min-width: 768px)";
function subscribeWide(onChange: () => void) {
  const mq = window.matchMedia(WIDE);
  mq.addEventListener("change", onChange);
  return () => mq.removeEventListener("change", onChange);
}

/**
 * Rows per page by screen. Below `md` every table row is a stacked card four or
 * five lines tall, so the same fifteen rows that fit a laptop screen are three
 * phone screens — the phone gets its own, smaller page.
 */
export function usePageSize(desktop: number, phone: number) {
  const wide = useSyncExternalStore(
    subscribeWide,
    () => window.matchMedia(WIDE).matches,
    () => true,
  );
  return wide ? desktop : phone;
}

const BTN =
  "rounded-md border border-hairline px-3 py-1.5 text-sm font-medium text-ink transition-colors hover:bg-hairline/40 disabled:cursor-not-allowed disabled:opacity-40";

/**
 * "11–20 de 46 · página 2 de 5" and the two buttons. Renders nothing for a
 * list that fits on one page — a pager with both buttons dead is chrome.
 */
export function Pager({
  page,
  pages,
  from,
  to,
  total,
  onPage,
  className = "mt-3",
}: {
  page: number;
  pages: number;
  from: number;
  to: number;
  total: number;
  onPage: (page: number) => void;
  className?: string;
}) {
  const prev = useRef<HTMLButtonElement>(null);
  const next = useRef<HTMLButtonElement>(null);
  if (pages <= 1) return null;

  // The button just pressed disables itself on the first/last page, and a
  // disabled button drops keyboard focus to <body>. Hand it to the other one.
  const goTo = (p: number) => {
    onPage(p);
    if (p <= 1) next.current?.focus();
    if (p >= pages) prev.current?.focus();
  };

  return (
    <div className={`flex flex-wrap items-center justify-between gap-3 ${className}`}>
      <p role="status" aria-live="polite" className="font-mono text-xs text-muted">
        {formatInt(from)}–{formatInt(to)} de {formatInt(total)} · página{" "}
        {formatInt(page)} de {formatInt(pages)}
      </p>
      <div className="flex items-center gap-2">
        <button ref={prev} type="button" disabled={page <= 1} onClick={() => goTo(page - 1)} className={BTN}>
          Anterior
        </button>
        <button ref={next} type="button" disabled={page >= pages} onClick={() => goTo(page + 1)} className={BTN}>
          Próxima
        </button>
      </div>
    </div>
  );
}
