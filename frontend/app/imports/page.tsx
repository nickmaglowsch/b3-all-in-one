"use client";

import { useEffect, useRef } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { isBusy } from "@/lib/jobStatus";
import {
  dependsOnImports,
  importsSettledSince,
  snapshotImports,
  type ImportSnapshot,
} from "@/lib/importDeps";
import type { ImportRow } from "@/lib/types";
import { PageHeader } from "@/components/ui/PageHeader";
import { Card } from "@/components/ui/Card";
import { Pager, usePageSize, usePaged } from "@/components/ui/Pager";
import { DataTable, type Column } from "@/components/ui/DataTable";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { IconUpload } from "@/components/icons";
import { formatDateTime } from "@/lib/format";
import { UploadZone } from "./UploadZone";

const KIND_LABEL: Record<string, string> = {
  negociacao: "Negociação",
  movimentacao: "Movimentação",
  posicao: "Posição",
  unknown: "Desconhecido",
};

const busyRow = (r: ImportRow) => isBusy(r.status);

/** When an import finishes, everything built from the extracts is stale —
 *  positions, trades, income, tax, index. Mark it so; the screens that are
 *  mounted refetch now, the rest on their next visit. */
function useRefreshBookOnImport(rows: ImportRow[] | undefined) {
  const qc = useQueryClient();
  const seen = useRef<ImportSnapshot | null>(null);
  useEffect(() => {
    if (!rows) return;
    if (importsSettledSince(seen.current, rows)) {
      qc.invalidateQueries({ predicate: (query) => dependsOnImports(query.queryKey) });
    }
    seen.current = snapshotImports(rows);
  }, [rows, qc]);
}

const columns: Column<ImportRow>[] = [
  {
    key: "filename",
    header: "Arquivo",
    mobile: "title",
    render: (r) => (
      // Truncated in the desktop table's fixed column; wrapped on a phone, where
      // the card is as tall as it needs to be and an ellipsis hides the one
      // thing that tells two monthly extracts apart. `break-all` because these
      // names have no spaces to break at.
      <span
        className="block break-all font-mono text-xs md:max-w-[22rem] md:truncate"
        title={r.filename}
      >
        {r.filename}
      </span>
    ),
  },
  {
    key: "kind",
    header: "Tipo",
    render: (r) => (
      <span className="inline-flex rounded-full border border-hairline px-2 py-0.5 text-xs text-muted">
        {KIND_LABEL[r.kind] ?? r.kind}
      </span>
    ),
  },
  { key: "status", header: "Situação", render: (r) => <StatusBadge status={r.status} /> },
  {
    key: "created_at",
    header: "Enviado em",
    align: "right",
    render: (r) => <span className="text-muted">{formatDateTime(r.created_at)}</span>,
  },
];

export default function ImportsPage() {
  const q = useQuery({
    queryKey: ["imports"],
    queryFn: () => api<ImportRow[]>("/imports"),
    // Poll only while something is still being processed; stop once all settled.
    refetchInterval: (query) =>
      (query.state.data ?? []).some(busyRow) ? 2000 : false,
  });
  useRefreshBookOnImport(q.data);

  const rows = q.data ?? []; // already newest-first: GET /imports orders desc

  return (
    <>
      <PageHeader eyebrow="Dados" title="Importações" />

      <div className="space-y-6">
        <UploadZone />

        {/* First visit: the how-to is the page. Once something is imported it
            is a reference, folded to one line so the list is not a screen
            below the upload zone. Folded while loading too: most visits are
            returning ones, and opening it until the list lands made the page
            jump up under the reader. */}
        {q.isSuccess && rows.length === 0 ? (
          <Card title="Onde baixar os extratos">
            <HowTo />
          </Card>
        ) : (
          <details className="group rounded-lg border border-hairline bg-panel">
            <summary className="cursor-pointer list-none px-3 py-3 text-sm font-semibold text-ink sm:px-4">
              <span className="mr-2 inline-block text-muted transition-transform group-open:rotate-90" aria-hidden>
                ›
              </span>
              Onde baixar os extratos
            </summary>
            <div className="border-t border-hairline p-3 sm:p-4">
              <HowTo />
            </div>
          </details>
        )}

        <Card title="Extratos enviados">
          {q.isLoading ? (
            <Skeleton className="h-40 w-full" />
          ) : rows.length === 0 ? (
            <EmptyState
              icon={<IconUpload size={28} />}
              title="Nenhum extrato importado ainda."
              hint="Envie os .xlsx de negociação e movimentação da B3 usando a área acima."
            />
          ) : (
            <ImportsTable rows={rows} />
          )}
        </Card>
      </div>
    </>
  );
}

// Ten at a time, newest first. Was a 28rem box with its own scrollbar inside the
// page's — two scrolls, and the inner one invisible until you hovered it.
function ImportsTable({ rows }: { rows: ImportRow[] }) {
  const paged = usePaged(rows, usePageSize(10, 5));
  return (
    <>
      <DataTable columns={columns} rows={paged.items} getRowKey={(r) => r.id} label="Extratos importados" />
      <Pager {...paged} onPage={paged.setPage} />
    </>
  );
}

function HowTo() {
  return (
    <>
      <ol className="ml-4 list-decimal space-y-1.5 text-sm text-ink">
        <li>
          Acesse a{" "}
          <span className="font-medium">Área do Investidor</span> da B3
          (investidor.b3.com.br) e entre com sua conta gov.br.
        </li>
        <li>
          Vá em <span className="font-medium">Extratos e Informativos</span> →{" "}
          <span className="font-medium">Negociação</span> e{" "}
          <span className="font-medium">Movimentação</span>, escolha o período e
          baixe cada um em Excel (.xlsx).
        </li>
        <li>Envie os dois tipos acima para montar posições, negociações e proventos.</li>
        <li>
          Para renda fixa ainda em carteira, baixe também o{" "}
          <span className="font-medium">Relatório Consolidado Mensal</span> (mesma
          tela). Ele traz o preço atualizado de cada CDB, LCI, LCA e CRI — é o que
          coloca esses contratos na rentabilidade sem você digitar a taxa. Um por
          mês; quanto mais meses, mais pontos na curva.
        </li>
      </ol>
      <p className="mt-4 border-t border-hairline pt-3 text-sm text-muted">
        Baixe o período mais longo possível. Se um extrato começar depois das suas
        primeiras compras, algumas vendas ficam sem custo de origem (histórico
        incompleto) e o cálculo de proventos e ganho de capital pode ficar
        impreciso.
      </p>
    </>
  );
}
