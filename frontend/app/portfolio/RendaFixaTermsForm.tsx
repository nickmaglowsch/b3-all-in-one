"use client";

import { useMemo, useState } from "react";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/api";
import type { RendaFixaTerm } from "@/lib/types";

/**
 * Fill in the indexer of a contract the index cannot price.
 *
 * A CDB that has been redeemed needs nothing: B3 reported both its aplicação
 * and its resgate, and two prices imply the rate exactly. One still held has
 * only its aplicação, and neither extract B3 publishes carries the indexer — so
 * the only source is the person who signed the paper. Rendered inside the
 * "o que ficou de fora" block, next to the contract it unblocks, because that
 * is where the user learns the gap exists.
 */

type Indexer = RendaFixaTerm["indexer"];

const INDEXERS: { value: Indexer; label: string; unit: string }[] = [
  { value: "cdi_pct", label: "% do CDI", unit: "%" },
  { value: "cdi_plus", label: "CDI +", unit: "% a.a." },
  { value: "ipca_plus", label: "IPCA +", unit: "% a.a." },
  { value: "pre", label: "Prefixado", unit: "% a.a." },
];

function useTerms() {
  return useQuery({
    queryKey: ["renda-fixa-terms"],
    queryFn: () => api<RendaFixaTerm[]>("/portfolio/settings/renda-fixa"),
    placeholderData: keepPreviousData,
  });
}

export function RendaFixaTermsForm({ tickers }: { tickers: string[] }) {
  const qc = useQueryClient();
  const terms = useTerms();
  const [draft, setDraft] = useState<Record<string, { indexer: Indexer; rate: string }>>({});

  const save = useMutation({
    mutationFn: (body: RendaFixaTerm[]) =>
      api<RendaFixaTerm[]>("/portfolio/settings/renda-fixa", {
        method: "PUT",
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      setDraft({});
      qc.invalidateQueries({ queryKey: ["renda-fixa-terms"] });
      // The chart is what this changes; without it the contract stays in the
      // very block the form is rendered inside.
      qc.invalidateQueries({ queryKey: ["portfolio-index"] });
    },
  });

  // PUT replaces the whole set, so a contract being edited here has to be sent
  // alongside every one already saved — otherwise saving one erases the rest.
  const existing = useMemo(
    () => Object.fromEntries((terms.data ?? []).map((t) => [t.ticker, t])),
    [terms.data],
  );

  const submit = (ticker: string) => {
    const d = draft[ticker];
    const rate = Number(d?.rate);
    if (!d?.indexer || !Number.isFinite(rate) || rate < 0) return;
    const next = { ...existing, [ticker]: { ticker, indexer: d.indexer, rate_pct: rate } };
    save.mutate(Object.values(next));
  };

  if (tickers.length === 0) return null;

  return (
    <div className="mt-3 border-t border-hairline pt-3">
      <p className="text-xs text-muted">
        A B3 não informa o indexador. Preencha e o contrato entra na curva, corrigido a
        partir do valor da sua própria aplicação.
      </p>
      <ul className="mt-2 space-y-2">
        {tickers.map((ticker) => {
          const d = draft[ticker] ?? { indexer: "cdi_pct", rate: "" };
          const unit = INDEXERS.find((i) => i.value === d.indexer)?.unit ?? "%";
          return (
            <li key={ticker} className="flex flex-wrap items-center gap-2">
              <span className="font-mono text-[11px] text-ink">{ticker}</span>
              <select
                aria-label={`Indexador de ${ticker}`}
                value={d.indexer}
                onChange={(e) =>
                  setDraft({
                    ...draft,
                    [ticker]: { ...d, indexer: e.target.value as Indexer },
                  })
                }
                className="rounded-[4px] border border-hairline bg-paper px-1.5 py-0.5 text-xs text-ink"
              >
                {INDEXERS.map((i) => (
                  <option key={i.value} value={i.value}>
                    {i.label}
                  </option>
                ))}
              </select>
              <input
                aria-label={`Taxa de ${ticker}`}
                type="number"
                inputMode="decimal"
                min={0}
                step="0.01"
                value={d.rate}
                onChange={(e) =>
                  setDraft({ ...draft, [ticker]: { ...d, rate: e.target.value } })
                }
                className="w-20 rounded-[4px] border border-hairline bg-paper px-1.5 py-0.5 text-right font-mono text-xs text-ink"
              />
              <span className="text-[11px] text-muted">{unit}</span>
              <button
                type="button"
                onClick={() => submit(ticker)}
                disabled={save.isPending || !draft[ticker]?.rate}
                className="rounded-[4px] border border-navy bg-navy px-2 py-0.5 text-[11px] text-white disabled:opacity-40"
              >
                Salvar
              </button>
            </li>
          );
        })}
      </ul>
      {save.error && (
        <p className="mt-2 text-xs text-loss" role="alert">
          {save.error instanceof ApiError ? save.error.message : "Falha ao salvar."}
        </p>
      )}
    </div>
  );
}
