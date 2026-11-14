"use client";

import { useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, errorText } from "@/lib/api";
import { IconUpload, IconSpinner, IconAlert } from "@/components/icons";

type Created = { id: string; filename: string; kind: string; status: string };

export function UploadZone() {
  const qc = useQueryClient();
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const mut = useMutation({
    mutationFn: (files: File[]) => {
      const fd = new FormData();
      for (const f of files) fd.append("files", f);
      return api<Created[]>("/imports", { method: "POST", body: fd });
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["imports"] }),
    onError: (e) => setError(errorText(e, "Falha ao enviar os arquivos.")),
  });

  // One upload at a time. A second drop used to start a second POST, and the
  // first one's settle then hid the second's "Enviando" line. The names shown
  // are the request's own variables, so they cannot belong to another upload.
  const busy = mut.isPending;
  const sending = busy ? (mut.variables ?? []).map((f) => f.name) : [];

  function submit(list: FileList | File[] | null) {
    if (busy) return;
    setError(null);
    const all = list ? Array.from(list) : [];
    if (all.length === 0) return;
    const isXlsx = (f: File) => f.name.toLowerCase().endsWith(".xlsx");
    const rejected = all.filter((f) => !isXlsx(f));
    const accepted = all.filter(isXlsx);
    if (rejected.length) {
      setError(
        `Apenas arquivos .xlsx são aceitos. Ignorado(s): ${rejected
          .map((f) => f.name)
          .join(", ")}`,
      );
    }
    if (accepted.length === 0) return;
    mut.mutate(accepted);
  }

  const pick = () => {
    if (!busy) inputRef.current?.click();
  };

  return (
    <div>
      <div
        onDragOver={(e) => {
          // preventDefault even while busy: otherwise the browser opens the
          // dropped file in place of the app.
          e.preventDefault();
          setDragging(!busy);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          submit(e.dataTransfer.files);
        }}
        onClick={pick}
        role="button"
        tabIndex={0}
        aria-disabled={busy}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") pick();
        }}
        className={`flex flex-col items-center justify-center rounded-lg border border-dashed px-6 py-12 text-center transition-colors ${dropZoneTone(busy, dragging)}`}
      >
        <div className="mb-3 text-muted">
          <IconUpload size={28} />
        </div>
        <p className="text-sm font-medium text-ink">
          Arraste seus extratos .xlsx aqui ou clique para escolher
        </p>
        <p className="mt-1 text-sm text-muted">
          {busy
            ? "Aguarde este envio terminar para mandar outros arquivos."
            : "Aceita vários arquivos de negociação e movimentação da B3."}
        </p>
        <input
          ref={inputRef}
          type="file"
          accept=".xlsx"
          multiple
          hidden
          disabled={busy}
          onChange={(e) => {
            submit(e.target.files);
            e.target.value = ""; // allow re-selecting the same file
          }}
        />
      </div>

      {error && (
        <p className="mt-3 flex items-start gap-1.5 text-sm text-status-error">
          <IconAlert size={15} className="mt-0.5 shrink-0" />
          <span>{error}</span>
        </p>
      )}

      {sending.length > 0 && (
        <div className="mt-3 flex items-center gap-2 rounded-md border border-hairline bg-panel px-3 py-2 text-sm text-status-processing">
          <IconSpinner size={15} />
          <span className="text-muted">
            Enviando {sending.length} arquivo(s): {sending.join(", ")}
          </span>
        </div>
      )}
    </div>
  );
}

function dropZoneTone(busy: boolean, dragging: boolean) {
  if (busy) return "cursor-not-allowed border-hairline bg-panel opacity-60";
  return dragging
    ? "cursor-pointer border-navy bg-navy/[0.04]"
    : "cursor-pointer border-hairline bg-panel hover:border-navy/40";
}
