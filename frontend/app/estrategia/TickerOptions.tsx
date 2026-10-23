import { useUserStrategies } from "@/lib/strategies";
import {
  specTickers,
  tickerOptions,
  tickerValue,
  type SleeveRow,
} from "@/lib/portfolioSleeves";

// The tickers of the carteira being edited (IVVB11.SA, …), as picker options.
// Without them a row holding one would show "selecione…" while still carrying
// the value. Read off the saved carteira, not only the rows, so a row moved off
// a ticker can be moved back. Only the carteira's own: ticker sleeves are added
// over the API/MCP (an AI building the built-ins' ETF/CDI blends), not here.
export function TickerOptions({ rows, editId }: { rows: SleeveRow[]; editId?: string }) {
  const userStrats = useUserStrategies();
  const saved = (userStrats.data ?? []).find((u) => u.id === editId);
  const tickers = tickerOptions(rows, saved ? specTickers(saved.spec) : []);
  if (tickers.length === 0) return null;
  return (
    <optgroup label="Ativos (ticker)">
      {tickers.map((t) => (
        <option key={t} value={tickerValue(t)} data-ticker={t}>
          {t}
        </option>
      ))}
    </optgroup>
  );
}
