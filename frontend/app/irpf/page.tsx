import { PageHeader } from "@/components/ui/PageHeader";
import { EmptyState } from "@/components/ui/EmptyState";
import { IconReceipt } from "@/components/icons";

// Stub — the IRPF feature agent replaces this file next wave.
export default function IrpfPage() {
  return (
    <>
      <PageHeader eyebrow="Imposto de renda" title="IRPF" />
      <EmptyState
        icon={<IconReceipt size={28} />}
        title="Nenhum relatório gerado — gere o relatório do ano."
        hint="Precisa dos extratos importados para apurar bens, proventos e ganhos de capital."
      />
    </>
  );
}
