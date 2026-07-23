import { PageHeader } from "@/components/ui/PageHeader";
import { EmptyState } from "@/components/ui/EmptyState";
import { IconWallet } from "@/components/icons";

// Stub — the portfolio feature agent replaces this file next wave.
export default function PortfolioPage() {
  return (
    <>
      <PageHeader eyebrow="Carteira" title="Portfólio" />
      <EmptyState
        icon={<IconWallet size={28} />}
        title="Nenhum extrato importado — envie seus arquivos da B3."
        hint="Depois de importar, suas posições, negociações e proventos aparecem aqui."
      />
    </>
  );
}
