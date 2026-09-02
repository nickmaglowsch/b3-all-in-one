"use client";

import { useMemo, useState } from "react";
import type { Strategy } from "@/lib/types";
import { Skeleton } from "@/components/ui/Skeleton";
import { EmptyState } from "@/components/ui/EmptyState";
import { kindLabel } from "./lib";
import { marketBadge, matchesQuery } from "@/lib/strategyGallery";

// Metadata chips share the ticker-chip geometry (DESIGN.md Shapes) at the 11px
// label step. Neutral by default: the series palette is data-only, so a chip
// only takes colour when the colour means something the label can't carry.
const badgeCls =
  "inline-flex items-center rounded-[4px] border px-1.5 py-0.5 font-mono text-[11px] font-medium uppercase leading-none tracking-wide";

export function KindBadge({ kind }: { kind: Strategy["kind"] }) {
  // espec = declarative spec, código = python plugin. The label carries the
  // distinction; it needs no hue of its own.
  return (
    <span className={`${badgeCls} border-hairline bg-paper text-muted`}>
      {kindLabel(kind)}
    </span>
  );
}

export function RuleBadge() {
  // A user strategy authored as a ranking rule rather than cloned from a spec.
  return (
    <span className={`${badgeCls} border-hairline bg-paper text-muted`}>
      regra
    </span>
  );
}

export function UserBadge() {
  // Distinguishes this tenant's own strategies from the built-in gallery —
  // ownership is the one distinction worth the interface accent.
  return (
    <span className={`${badgeCls} border-navy/25 bg-navy/[0.06] text-navy`}>
      minha
    </span>
  );
}

export function MarketBadge({ label }: { label: string }) {
  // Which plane the strategy trades. Neutral like KindBadge: the label carries
  // the distinction and it needs no hue. Whether a row gets one at all is
  // lib/strategyGallery.marketBadge's decision, not this component's.
  return (
    <span className={`${badgeCls} border-hairline bg-paper text-muted`}>
      {label}
    </span>
  );
}

export function FundamentalsBadge() {
  // Strategy reads CVM fundamentals — surfaced so users know it depends on that
  // data being ingested (a fundamentals-less environment can't build its target).
  // A caveat, so it takes the status palette, not a chart series.
  return (
    <span
      className={`${badgeCls} border-status-processing/40 bg-status-processing/10 text-ink`}
    >
      fundamentos
    </span>
  );
}

export function StrategyGallery({
  strategies,
  loading,
  selected,
  onSelect,
}: {
  strategies: Strategy[];
  loading: boolean;
  selected: string | null;
  onSelect: (name: string) => void;
}) {
  const [q, setQ] = useState("");

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    if (!needle) return strategies;
    return strategies.filter((s) => matchesQuery(s, needle));
  }, [strategies, q]);

  return (
    <div className="flex h-full flex-col">
      <div className="mb-3">
        <input
          type="search"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Buscar estratégia…"
          className="w-full rounded-md border border-hairline bg-paper px-3 py-2 text-sm text-ink placeholder:text-muted focus:border-navy focus:outline-none"
        />
        <p className="mt-1.5 font-mono text-[11px] text-muted">
          {loading ? "carregando…" : `${filtered.length} de ${strategies.length} estratégias`}
        </p>
      </div>

      {loading ? (
        <div className="space-y-2">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-16 w-full" />
          ))}
        </div>
      ) : filtered.length === 0 ? (
        <EmptyState
          title="Nenhuma estratégia corresponde à busca."
          hint="Tente outro termo — nome ou descrição."
        />
      ) : (
        <ul className="max-h-[560px] space-y-1.5 overflow-y-auto pr-1">
          {filtered.map((s) => {
            const active = s.name === selected;
            const market = marketBadge(s);
            return (
              <li key={s.name}>
                <button
                  type="button"
                  onClick={() => onSelect(s.name)}
                  aria-pressed={active}
                  className={`w-full rounded-md border px-3 py-2.5 text-left transition-colors ${
                    active
                      ? "border-navy bg-navy/[0.05] ring-1 ring-navy"
                      : "border-hairline bg-panel hover:border-navy/40 hover:bg-hairline/30"
                  }`}
                >
                  <div className="flex items-start justify-between gap-2">
                    <span className="text-sm font-semibold leading-snug text-ink">
                      {s.name}
                    </span>
                    <span className="flex shrink-0 items-center gap-1">
                      {s.source === "user" && <UserBadge />}
                      {market && <MarketBadge label={market} />}
                      {s.needs_fundamentals && <FundamentalsBadge />}
                      <KindBadge kind={s.kind} />
                    </span>
                  </div>
                  <p className="mt-1 line-clamp-2 text-xs leading-snug text-muted">
                    {s.description}
                  </p>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
