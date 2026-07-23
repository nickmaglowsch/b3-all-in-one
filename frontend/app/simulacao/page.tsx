import { PageHeader } from "@/components/ui/PageHeader";
import { EmptyState } from "@/components/ui/EmptyState";
import { IconClock } from "@/components/icons";

// Stub — the what-if feature agent replaces this file.
export default function SimulacaoPage() {
  return (
    <>
      <PageHeader eyebrow="E se" title="Simulação" />
      <EmptyState
        icon={<IconClock size={28} />}
        title="Compare o desempenho real da sua carteira com estratégias alternativas."
        hint="Reaplica seus aportes reais em outra estratégia, do primeiro trade até hoje."
      />
    </>
  );
}
