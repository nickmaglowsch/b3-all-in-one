"use client";

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { PageHeader } from "@/components/ui/PageHeader";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { IconReceipt } from "@/components/icons";
import { IrpfReport } from "./IrpfReport";
import { DarfLedger } from "./DarfLedger";
import { useYearChoice, YearSelect } from "./YearSelect";
import { anoBasePadrao } from "@/lib/years";

interface IrpfResponse {
  year: number;
  markdown: string;
}

export default function IrpfPage() {
  // The ano-base being declared right now is last year's; the DARF ledger below
  // has its own picker, because its natural year is the current one.
  const [year, setChosen] = useYearChoice(anoBasePadrao);
  const [generated, setGenerated] = useState<IrpfResponse | null>(null);
  // A report is only shown under the year it was generated for — otherwise the
  // picker says 2024 while the 2025 report sits below it (and gets printed).
  const report = generated?.year === year ? generated : null;

  const gen = useMutation({
    mutationFn: (y: number) =>
      api<IrpfResponse>("/irpf/reports", {
        method: "POST",
        body: JSON.stringify({ year: y }),
      }),
    onSuccess: (data) => setGenerated(data),
  });

  return (
    <>
      <PageHeader eyebrow="Imposto de renda" title="IRPF">
        <YearSelect
          id="irpf-year"
          label="Ano-base"
          optionLabel={(y) => `Ano-base ${y}`}
          value={year}
          onChange={setChosen}
          disabled={gen.isPending}
        />
        <button
          type="button"
          onClick={() => year !== null && gen.mutate(year)}
          disabled={gen.isPending || year === null}
          className="rounded-md bg-navy px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-navy/90 disabled:opacity-60 print:hidden"
        >
          {gen.isPending ? "Gerando…" : "Gerar relatório"}
        </button>
        {report && <ExportButtons report={report} />}
      </PageHeader>

      {/* The one line that must survive into the PDF: it is the disclaimer, and
          a printed tax report that drops it is the version that gets forwarded
          to an accountant as if it were advice. */}
      <p className="mb-6 flex items-start gap-2 text-xs text-muted">
        <span aria-hidden>ⓘ</span>
        Confira os valores no informe de rendimentos. Este relatório não é
        aconselhamento tributário.
      </p>

      <div className="mb-8 print:hidden">
        <DarfLedger />
      </div>

      {gen.isPending ? (
        <ReportSkeleton />
      ) : gen.isError ? (
        <div className="rounded-lg border border-loss/30 bg-loss/[0.04] p-4">
          <p className="text-sm font-medium text-loss" role="alert">
            Não foi possível gerar o relatório.
          </p>
          <p className="mt-1 text-sm text-muted">
            {gen.error instanceof Error ? gen.error.message : "Erro inesperado."}
          </p>
        </div>
      ) : report ? (
        <IrpfReport markdown={report.markdown} />
      ) : (
        <EmptyState
          icon={<IconReceipt size={28} />}
          title="Nenhum relatório gerado — escolha o ano-base e gere o relatório."
          hint="Bens e Direitos, proventos e ganhos de capital, prontos para colar no programa do IRPF."
        />
      )}
    </>
  );
}

/**
 * Markdown and PDF export (spec/03, "full markdown/PDF export").
 *
 * The PDF is the browser's own print-to-PDF over the report already rendered on
 * this page, styled by the `@media print` block in globals.css. That is a
 * stylesheet instead of WeasyPrint plus Pango, Cairo and a font stack in an
 * arm64 image — and it prints exactly what the user is looking at, which is the
 * property that matters for a document they are about to copy into the IRPF
 * program. The .md is the same string the API returned, as a Blob.
 */
function ExportButtons({ report }: { report: IrpfResponse }) {
  function downloadMarkdown() {
    const url = URL.createObjectURL(
      new Blob([report.markdown], { type: "text/markdown;charset=utf-8" }),
    );
    const a = document.createElement("a");
    a.href = url;
    a.download = `irpf-ano-base-${report.year}.md`;
    a.click();
    // Not synchronously: Safari and older Firefox start the download after
    // click() returns, and a revoked URL cancels it (same as app/conta).
    setTimeout(() => URL.revokeObjectURL(url), 0);
  }

  const cls =
    "rounded-md border border-hairline px-3 py-2 text-sm font-medium text-muted transition-colors hover:bg-hairline/40 hover:text-ink print:hidden";

  return (
    <>
      <button type="button" onClick={() => window.print()} className={cls}>
        Baixar PDF
      </button>
      <button type="button" onClick={downloadMarkdown} className={cls}>
        Baixar .md
      </button>
    </>
  );
}

// The report takes ~2–5s to compute server-side. Mimic the two-column layout so
// the page doesn't jump when it lands.
function ReportSkeleton() {
  return (
    <div className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_190px]">
      <div className="space-y-4">
        <Skeleton className="h-6 w-64" />
        <Skeleton className="h-4 w-full max-w-xl" />
        <Skeleton className="h-4 w-full max-w-md" />
        <div className="pt-4">
          <Skeleton className="h-5 w-48" />
        </div>
        <Skeleton className="h-40 w-full" />
        <Skeleton className="h-40 w-full" />
      </div>
      <div className="hidden lg:block">
        <Skeleton className="h-4 w-16" />
        <div className="mt-3 space-y-2">
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-full" />
        </div>
      </div>
    </div>
  );
}
