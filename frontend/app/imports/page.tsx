import { PageHeader } from "@/components/ui/PageHeader";
import { EmptyState } from "@/components/ui/EmptyState";
import { IconUpload } from "@/components/icons";

// Stub — the imports feature agent replaces this file next wave.
export default function ImportsPage() {
  return (
    <>
      <PageHeader eyebrow="Dados" title="Importações" />
      <EmptyState
        icon={<IconUpload size={28} />}
        title="Nenhum extrato importado — envie seus arquivos da B3."
        hint="Aceita os .xlsx de negociação e movimentação exportados pela área do investidor."
      />
    </>
  );
}
