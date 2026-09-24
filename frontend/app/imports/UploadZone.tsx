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
  const [pending, setPending] = useState<string[]>([]);

  const mut = useMutation({
    mutationFn: (files: File[]) => {
      const fd = new FormData();
      for (const f of files) fd.append("files", f);
      return api<Created[]>("/imports", { method: "POST", body: fd });
    },
    onSuccess: () => {
      setPending([]);
      qc.invalidateQueries({ queryKey: ["imports"] });
    },
    onError: (e) => {
      setPending([]);
      setError(errorText(e, "Falha ao enviar os arquivos."));
    },
  });

  function submit(list: FileList | File[] | null) {
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
    setPending(accepted.map((f) => f.name));
    mut.mutate(accepted);
  }

  return (
    <div>
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          submit(e.dataTransfer.files);
        }}
        onClick={() => inputRef.current?.click()}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") inputRef.current?.click();
        }}
        className={`flex cursor-pointer flex-col items-center justify-center rounded-lg border border-dashed px-6 py-12 text-center transition-colors ${
          dragging
            ? "border-navy bg-navy/[0.04]"
            : "border-hairline bg-panel hover:border-navy/40"
        }`}
      >
        <div className="mb-3 text-muted">
          <IconUpload size={28} />
        </div>
        <p className="text-sm font-medium text-ink">
          Arraste seus extratos .xlsx aqui ou clique para escolher
        </p>
        <p className="mt-1 text-sm text-muted">
          Aceita vários arquivos de negociação e movimentação da B3.
        </p>
        <input
          ref={inputRef}
          type="file"
          accept=".xlsx"
          multiple
          hidden
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

      {mut.isPending && pending.length > 0 && (
        <div className="mt-3 flex items-center gap-2 rounded-md border border-hairline bg-panel px-3 py-2 text-sm text-status-processing">
          <IconSpinner size={15} />
          <span className="text-muted">
            Enviando {pending.length} arquivo(s): {pending.join(", ")}
          </span>
        </div>
      )}
    </div>
  );
}
