import { tickerLabel, tickerRefs, type SleeveRow } from "@/lib/portfolioSleeves";

// The tickers a saved carteira already holds (IVVB11.SA, …), as picker options.
// Without them a row holding one would show "selecione…" while still carrying
// the value. Only the carteira's own: ticker sleeves are added over the API/MCP
// (an AI building the built-ins' ETF/CDI blends), not from this picker.
export function TickerOptions({ rows }: { rows: SleeveRow[] }) {
  const refs = tickerRefs(rows);
  if (refs.length === 0) return null;
  return (
    <optgroup label="Ativos (ticker)">
      {refs.map((r) => (
        <option key={r} value={r}>
          {tickerLabel(r)}
        </option>
      ))}
    </optgroup>
  );
}
