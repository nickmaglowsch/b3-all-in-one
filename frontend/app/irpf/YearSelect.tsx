"use client";

import { useState } from "react";
import { useBrowserValue } from "@/lib/hooks";
import { anoCorrente, anosRecentes } from "@/lib/years";

/** The current year in São Paulo, or null during the static prerender. Read from
 *  the browser so a build made in December does not ship last year's list —
 *  the prerendered HTML has no year in it at all. */
export function useAnoCorrente(): number | null {
  return useBrowserValue(anoCorrente, null);
}

/** The year a picker shows: the user's pick, else `padrao` of the current year
 *  (null until the clock is known). */
export function useYearChoice(padrao: (atual: number) => number) {
  const atual = useAnoCorrente();
  const [chosen, setChosen] = useState<number | null>(null);
  const year = chosen ?? (atual === null ? null : padrao(atual));
  return [year, setChosen] as const;
}

/** A year picker over the last few years, current one included. Empty (and
 *  disabled) until the clock is known. */
export function YearSelect({
  id,
  label,
  optionLabel,
  value,
  onChange,
  disabled,
}: {
  id: string;
  /** Accessible name — the visible options say what the year is for. */
  label: string;
  optionLabel: (year: number) => string;
  value: number | null;
  onChange: (year: number) => void;
  disabled?: boolean;
}) {
  const atual = useAnoCorrente();
  const years = atual === null ? [] : anosRecentes(atual);
  return (
    <>
      <label className="sr-only" htmlFor={id}>
        {label}
      </label>
      <select
        id={id}
        value={value ?? ""}
        onChange={(e) => onChange(Number(e.target.value))}
        disabled={disabled || value === null}
        className="rounded-md border border-hairline bg-panel px-3 py-2 text-sm font-medium text-ink disabled:opacity-60 print:hidden"
      >
        {years.map((y) => (
          <option key={y} value={y}>
            {optionLabel(y)}
          </option>
        ))}
      </select>
    </>
  );
}
