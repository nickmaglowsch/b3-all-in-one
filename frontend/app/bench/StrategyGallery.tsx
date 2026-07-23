"use client";

import { useMemo, useState } from "react";
import type { Strategy } from "@/lib/types";
import { Skeleton } from "@/components/ui/Skeleton";
import { EmptyState } from "@/components/ui/EmptyState";
import { kindLabel } from "./lib";

export function KindBadge({ kind }: { kind: Strategy["kind"] }) {
  // espec = declarative spec, código = python plugin. Colour + label, never colour alone.
  const spec = kind === "spec";
  return (
    <span
      className={`inline-flex items-center rounded-[4px] px-1.5 py-0.5 font-mono text-[10px] font-semibold uppercase tracking-wide ${
        spec
          ? "bg-series-3/10 text-series-3"
          : "bg-series-7/10 text-series-7"
      }`}
    >
      {kindLabel(kind)}
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
    return strategies.filter(
      (s) =>
        s.name.toLowerCase().includes(needle) ||
        s.description.toLowerCase().includes(needle),
    );
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
                    <KindBadge kind={s.kind} />
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
