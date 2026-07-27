"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { ImportRow } from "@/lib/types";
import { PageHeader } from "@/components/ui/PageHeader";
import { Card } from "@/components/ui/Card";
import { DataTable, type Column } from "@/components/ui/DataTable";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { IconUpload } from "@/components/icons";
import { formatDate } from "@/lib/format";
import { UploadZone } from "./UploadZone";

const KIND_LABEL: Record<string, string> = {
  negociacao: "Negociação",
  movimentacao: "Movimentação",
  unknown: "Desconhecido",
};

const isBusy = (r: ImportRow) =>
  r.status === "pending" || r.status === "processing" || r.status === "running";

const columns: Column<ImportRow>[] = [
  {
    key: "filename",
    header: "Arquivo",
    render: (r) => (
      <span className="block max-w-[22rem] truncate font-mono text-xs" title={r.filename}>
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
    render: (r) => <span className="text-muted">{formatDate(r.created_at)}</span>,
  },
];

export default function ImportsPage() {
  const q = useQuery({
    queryKey: ["imports"],
    queryFn: () => api<ImportRow[]>("/imports"),
    // Poll only while something is still being processed; stop once all settled.
    refetchInterval: (query) =>
      (query.state.data ?? []).some(isBusy) ? 2000 : false,
  });

  const rows = q.data ?? []; // already newest-first: GET /imports orders desc

  return (
    <>
      <PageHeader eyebrow="Dados" title="Importações" />

      <div className="space-y-6">
        <UploadZone />

        <Card title="Onde baixar os extratos">
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
          </ol>
          <p className="mt-4 border-t border-hairline pt-3 text-sm text-muted">
            Baixe o período mais longo possível. Se um extrato começar depois das suas
            primeiras compras, algumas vendas ficam sem custo de origem (histórico
            incompleto) e o cálculo de proventos e ganho de capital pode ficar
            impreciso.
          </p>
        </Card>

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
            <div className="max-h-[28rem] overflow-y-auto">
              <DataTable columns={columns} rows={rows} getRowKey={(r) => r.id} label="Extratos importados" />
            </div>
          )}
        </Card>
      </div>
    </>
  );
}
