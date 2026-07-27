/** Stands in for a chart with nothing to draw.
 *
 * recharts renders bare axes for an empty series, which reads as "your return
 * was zero" rather than "this window is empty" — and every date filter can
 * produce an empty window (an inverted range, a span of holidays). Sized from
 * the same `height` the chart would take so nothing below it jumps.
 */
export function NoData({
  height,
  label = "Nenhum ponto no período selecionado.",
}: {
  height: number;
  label?: string;
}) {
  return (
    <div
      style={{ width: "100%", height }}
      className="flex items-center justify-center rounded-md border border-dashed border-hairline text-sm text-muted"
    >
      {label}
    </div>
  );
}
