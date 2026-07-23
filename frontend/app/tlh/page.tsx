import { PageHeader } from "@/components/ui/PageHeader";
import { EmptyState } from "@/components/ui/EmptyState";
import { IconScissors } from "@/components/icons";

// Stub — the TLH feature agent replaces this file next wave.
export default function TlhPage() {
  return (
    <>
      <PageHeader eyebrow="Colheita de prejuízo" title="TLH" />
      <EmptyState
        icon={<IconScissors size={28} />}
        title="Nenhum plano de TLH — simule vendas para colher prejuízo."
        hint="Informe as vendas e veja o efeito fiscal antes de executar qualquer coisa."
      />
    </>
  );
}
