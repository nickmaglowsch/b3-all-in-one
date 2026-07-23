import { PageHeader } from "@/components/ui/PageHeader";
import { EmptyState } from "@/components/ui/EmptyState";
import { IconCompass } from "@/components/icons";

// Stub — the strategy feature agent replaces this file next wave.
export default function EstrategiaPage() {
  return (
    <>
      <PageHeader eyebrow="Alocação" title="Estratégia" />
      <EmptyState
        icon={<IconCompass size={28} />}
        title="Nenhum sleeve configurado — defina sua alocação-alvo."
        hint="Combine estratégias da galeria com um percentual em cada, somando 100%."
      />
    </>
  );
}
