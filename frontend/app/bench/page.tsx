import { PageHeader } from "@/components/ui/PageHeader";
import { EmptyState } from "@/components/ui/EmptyState";
import { IconChart } from "@/components/icons";

// Stub — the bench feature agent replaces this file next wave.
export default function BenchPage() {
  return (
    <>
      <PageHeader eyebrow="Backtests" title="Bench" />
      <EmptyState
        icon={<IconChart size={28} />}
        title="Nenhum backtest — rode uma estratégia da galeria."
        hint="Cada rodada mostra retorno, drawdown e giro, antes e depois de impostos."
      />
    </>
  );
}
