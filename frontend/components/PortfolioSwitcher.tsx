"use client";

import { useEffect, useSyncExternalStore } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  clearPortfolioId,
  getPortfolioId,
  setPortfolioId,
  subscribePortfolio,
} from "@/lib/api";
import { usePortfolios } from "@/lib/hooks";

// A native <select>: the whole control is "pick one of a short list", which the
// platform already does with keyboard, mobile wheel and screen reader for free.
export function PortfolioSwitcher() {
  const qc = useQueryClient();
  const { data } = usePortfolios();
  // The server snapshot is null because the static export has no localStorage at
  // build time; subscribing means a switch made on the carteiras page shows here.
  const active = useSyncExternalStore(subscribePortfolio, getPortfolioId, () => null);

  const self = data?.find((p) => p.is_self);
  const stale = Boolean(data && active && !data.some((p) => p.id === active));

  // A book that was deleted, or belongs to another login, must not wedge every
  // request behind a 404. The list is the authority, so drop what it lacks.
  useEffect(() => {
    if (!stale) return;
    clearPortfolioId();
    qc.clear();
  }, [stale, qc]);

  // One book is the normal case below the enterprise tier: nothing to switch.
  if (!data || data.length < 2 || !self) return null;

  function pick(id: string) {
    if (id === self!.id) clearPortfolioId();
    else setPortfolioId(id);
    // Every cached query holds the previous book's rows, and none of the keys
    // mention which book they came from. Dropping the lot is the honest reset.
    qc.clear();
  }

  return (
    <div className="px-3 pb-3">
      <label
        htmlFor="portfolio-switcher"
        className="mb-1.5 block font-mono text-[11px] font-medium uppercase tracking-[0.14em] text-muted"
      >
        Carteira
      </label>
      <select
        id="portfolio-switcher"
        value={active ?? self.id}
        onChange={(e) => pick(e.target.value)}
        className="w-full truncate rounded-md border border-hairline bg-paper px-3 py-2 text-sm text-ink focus:border-navy focus:outline-none"
      >
        {data.map((p) => (
          <option key={p.id} value={p.id}>
            {p.label}
          </option>
        ))}
      </select>
    </div>
  );
}
