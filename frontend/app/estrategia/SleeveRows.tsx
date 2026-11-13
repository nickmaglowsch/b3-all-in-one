"use client";

// The sleeve editor's rows: a searchable picker per row (strategies, Caixa, and
// any listed asset on B3, the US or crypto), its percentage, and remove. Split
// from page.tsx, which sits at the module-size gate. What the picker offers and
// what a choice writes on the row is lib/portfolioSleeves.ts, under `npm test`.
import { useEffect, useMemo, useState } from "react";
import { Combobox } from "@/components/ui/Combobox";
import {
  heldAssets,
  pickerOptions,
  pickerRefs,
  rowLabel,
  specTickers,
  type PickedAsset,
  type SleeveOption,
  type SleeveRow,
} from "@/lib/portfolioSleeves";
import { useUserStrategies } from "@/lib/strategies";
import { ASSET_SEARCH_MIN, useAssetSearch, useStrategies } from "./lib";

interface Refs {
  builtinRefs: string[];
  userRefs: string[];
  needsFunds: ReadonlySet<string>;
}

// The gallery as the picker splits it. A user fixed_weight portfolio can't be a
// sleeve of another (cycle guard — server 422s), and a US or crypto *strategy*
// is refused as a sleeve (strategy_specs off_b3_refs) — pickerRefs drops both.
// Strategies that read CVM fundamentals carry a note, so the choice is informed.
function useRefs(): Refs {
  const strategies = useStrategies();
  const userStrats = useUserStrategies();
  return useMemo(() => {
    const portfolios = new Set(
      (userStrats.data ?? []).filter((u) => u.kind === "fixed_weight").map((u) => u.name),
    );
    const gallery = strategies.data ?? [];
    return {
      ...pickerRefs(gallery, portfolios),
      needsFunds: new Set(gallery.filter((s) => s.needs_fundamentals).map((s) => s.name)),
    };
  }, [strategies.data, userStrats.data]);
}

export function SleeveRows({
  rows,
  editId,
  onPick,
  onPct,
  onRemove,
}: {
  rows: SleeveRow[];
  editId?: string;
  onPick: (i: number, pick: SleeveOption["pick"]) => void;
  onPct: (i: number, pct: string) => void;
  onRemove: (i: number) => void;
}) {
  const refs = useRefs();
  const userStrats = useUserStrategies();
  // The carteira being edited keeps its own tickers pickable, even after a row
  // moves off one; the live editor offers the ones its rows hold.
  const saved = (userStrats.data ?? []).find((u) => u.id === editId);
  const held = useMemo(() => heldAssets(rows, saved ? specTickers(saved.spec) : []), [rows, saved]);
  return (
    <>
      {rows.map((r, i) => (
        <div key={i} className="flex items-center gap-2">
          <SleevePicker
            row={r}
            refs={refs}
            held={held}
            carteira={!!editId}
            onPick={(pick) => onPick(i, pick)}
          />
          <div className="flex items-center gap-1">
            <input
              type="number"
              min={0}
              max={100}
              step={1}
              value={r.target_pct}
              aria-label={`Percentual de ${rowLabel(r) || "sleeve"}`}
              onChange={(e) => onPct(i, e.target.value)}
              className="w-20 rounded-md border border-hairline bg-paper px-2 py-1.5 text-right font-mono text-sm tnum text-ink"
            />
            <span className="font-mono text-sm text-muted">%</span>
          </div>
          <button
            type="button"
            onClick={() => onRemove(i)}
            aria-label="Remover sleeve"
            className="rounded-md border border-hairline px-2 py-1.5 text-sm text-muted hover:bg-hairline/40"
          >
            ✕
          </button>
        </div>
      ))}
    </>
  );
}

// Typing is one request per pause, not one per keystroke.
function useDebounced(value: string, ms: number): string {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setSettled(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return settled;
}

function SleevePicker({
  row,
  refs,
  held,
  carteira,
  onPick,
}: {
  row: SleeveRow;
  refs: Refs;
  held: PickedAsset[];
  carteira: boolean;
  onPick: (pick: SleeveOption["pick"]) => void;
}) {
  const [query, setQuery] = useState("");
  const term = useDebounced(query.trim(), 250);
  const search = useAssetSearch(term);
  const hits = term.length >= ASSET_SEARCH_MIN ? search.data?.results : undefined;
  const options = useMemo(
    () => pickerOptions({ query, ...refs, held, hits: hits ?? [], carteira }),
    [query, refs, held, hits, carteira],
  );
  return (
    <Combobox
      label="Estratégia ou ativo do sleeve"
      value={rowLabel(row)}
      placeholder="Buscar estratégia ou ativo (B3, EUA, cripto)…"
      query={query}
      onQueryChange={setQuery}
      options={options}
      onSelect={(o) => onPick(o.pick)}
      notice={searchNotice(query, search)}
      emptyText="Nada encontrado."
    />
  );
}

// What the asset half of the list is doing, said under it.
function searchNotice(
  query: string,
  search: { isFetching: boolean; isError: boolean },
): string | undefined {
  if (query.trim().length < ASSET_SEARCH_MIN) {
    return "Digite 2 letras ou mais para buscar ativos da B3, dos EUA e cripto.";
  }
  if (search.isError) return "Busca de ativos indisponível agora; as estratégias seguem listadas.";
  return search.isFetching ? "Buscando ativos…" : undefined;
}
