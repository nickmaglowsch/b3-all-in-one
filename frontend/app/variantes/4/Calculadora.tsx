"use client";

import { useState } from "react";

import { ALIQUOTA, ISENCAO, impostoMes } from "@/lib/apuracao";
import { formatMoney, formatPercent } from "@/lib/format";

// The hero interaction: two sliders and the figure they produce. Same rule the
// engine applies to a real extract — the isenção looks at the total sold, not at
// the gain — which is exactly the part a planilha gets wrong.
export function Calculadora() {
  const [vendas, setVendas] = useState(24000);
  const [ganhoRaw, setGanho] = useState(2500);
  const ganho = Math.min(ganhoRaw, vendas);
  const imposto = impostoMes(vendas, ganho);
  const isento = vendas <= ISENCAO;

  return (
    <div className="grid overflow-hidden rounded-lg border border-hairline bg-panel lg:grid-cols-[1fr_20rem]">
      <div className="p-5 md:p-6">
        <Slider
          label="Vendas de ações no mês"
          value={vendas}
          min={0}
          max={100000}
          step={1000}
          onChange={setVendas}
        />
        {/* The threshold, drawn: the bar crossing the tick is the moment the
            month becomes taxable. A number alone does not show a crossing. */}
        <div className="mt-3">
          <div aria-hidden className="relative h-1.5 overflow-hidden rounded-full bg-hairline/60">
            <div
              className={`h-full rounded-full ${isento ? "bg-series-3" : "bg-navy"}`}
              style={{ width: `${Math.min(100, (vendas / 100000) * 100)}%` }}
            />
            <span className="absolute inset-y-0 left-[20%] w-px bg-ink" />
          </div>
          <p className="mt-2 font-mono text-[11px] uppercase tracking-[0.14em] text-muted">
            Isenção mensal · {formatMoney(ISENCAO)} ·{" "}
            <span className={isento ? "text-gain" : "text-ink"}>
              {isento ? "dentro" : "ultrapassada"}
            </span>
          </p>
        </div>

        <div className="mt-7">
          <Slider
            label="Ganho nessas vendas"
            value={ganho}
            min={0}
            max={20000}
            step={250}
            onChange={setGanho}
          />
        </div>

        <p className="mt-6 border-t border-hairline pt-4 text-sm leading-relaxed text-muted">
          Ações em operação comum. O app aplica a mesma regra aos seus extratos — com preço médio,
          eventos e prejuízo acumulado, que um slider não sabe.
        </p>
      </div>

      <div className="flex flex-col justify-center gap-1 border-t border-hairline bg-paper p-5 md:p-6 lg:border-l lg:border-t-0">
        <p className="font-mono text-[11px] font-medium uppercase tracking-[0.14em] text-muted">
          {isento ? "Imposto do mês" : "DARF · código 6015"}
        </p>
        <p
          className={`font-display text-4xl font-extrabold leading-none tracking-tight tnum ${
            isento ? "text-gain" : "text-ink"
          }`}
        >
          {isento ? "Isento" : formatMoney(imposto)}
        </p>
        <p className="mt-2 text-sm leading-relaxed text-muted">
          {isento
            ? `Vendeu ${formatMoney(vendas)} — dentro da isenção, nada a pagar.`
            : `${formatPercent(ALIQUOTA)} sobre ${formatMoney(ganho)} de ganho, porque as vendas passaram de ${formatMoney(ISENCAO)}.`}
        </p>
      </div>
    </div>
  );
}

function Slider({
  label,
  value,
  min,
  max,
  step,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  onChange: (v: number) => void;
}) {
  return (
    <label className="block">
      <span className="flex flex-wrap items-baseline justify-between gap-2">
        <span className="text-sm text-ink">{label}</span>
        <span className="font-display text-xl font-extrabold tracking-tight text-ink tnum">
          {formatMoney(value)}
        </span>
      </span>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="mt-2 w-full accent-navy"
      />
    </label>
  );
}
