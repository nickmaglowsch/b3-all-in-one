"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useState } from "react";
import {
  api,
  ApiError,
  clearPortfolioId,
  getPortfolioId,
  setPortfolioId,
} from "@/lib/api";
import { usePortfolios, useSubscription } from "@/lib/hooks";
import { EMPRESARIAL } from "@/lib/modules";
import type { Portfolio, Subscription } from "@/lib/types";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { PageHeader } from "@/components/ui/PageHeader";
import { IconTrash, IconUsers } from "@/components/icons";

const temEmpresarial = (sub?: Subscription) => !!sub?.modules.includes(EMPRESARIAL);

export default function CarteirasPage() {
  const qc = useQueryClient();
  const router = useRouter();
  const { data, isLoading } = usePortfolios();
  const sub = useSubscription();
  const [error, setError] = useState<string | null>(null);
  const [label, setLabel] = useState("");

  // A 402 already opens the upgrade dialog from lib/api — only the rest is ours
  // to show. Any write invalidates the list; switching books is local state.
  const write = useMutation({
    mutationFn: (req: { path: string; method: string; label?: string }) =>
      api<Portfolio | undefined>(req.path, {
        method: req.method,
        body: req.label === undefined ? undefined : JSON.stringify({ label: req.label }),
      }),
    onMutate: () => setError(null),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["portfolios"] }),
    onError: (e) => {
      // A 402 already opened the upgrade dialog; a red line behind it would be
      // the same sentence twice.
      if (e instanceof ApiError && e.status === 402) return;
      setError(e instanceof Error ? e.message : "Erro inesperado.");
    },
  });

  const managed = data?.filter((p) => !p.is_self) ?? [];
  const isEnterprise = temEmpresarial(sub.data);

  function create(e: React.FormEvent) {
    e.preventDefault();
    if (!label.trim()) return;
    write.mutate(
      { path: "/portfolios", method: "POST", label: label.trim() },
      { onSuccess: () => setLabel("") },
    );
  }

  function rename(p: Portfolio, next: string) {
    if (!next.trim() || next.trim() === p.label) return;
    write.mutate({ path: `/portfolios/${p.id}`, method: "PATCH", label: next.trim() });
  }

  function remove(p: Portfolio) {
    if (
      !window.confirm(
        `Excluir “${p.label}” e todos os dados dessa carteira? Não dá para desfazer.`,
      )
    )
      return;
    // Acting on a book that is about to stop existing would 404 every request.
    if (getPortfolioId() === p.id) {
      clearPortfolioId();
      qc.clear();
    }
    write.mutate({ path: `/portfolios/${p.id}`, method: "DELETE" });
  }

  function open(p: Portfolio) {
    if (p.is_self) clearPortfolioId();
    else setPortfolioId(p.id);
    qc.clear();
    // Opening a client's book means going to look at it. The sidebar switcher
    // follows on its own — it subscribes to the selection rather than copying it.
    router.push("/portfolio");
  }

  return (
    <>
      <PageHeader title="Carteiras" eyebrow="Conta" />

      {!isEnterprise && !sub.isLoading && (
        <Card className="mb-6">
          <p className="text-sm text-ink">
            Você administra uma carteira — a sua.
          </p>
          <p className="mt-1 text-sm text-muted">
            O plano Empresarial abre uma carteira por cliente, com dados
            separados e a mesma apuração de IRPF em cada uma. Pensado para
            assessores e escritórios.
          </p>
        </Card>
      )}

      {error && (
        <p
          className="mb-4 rounded-md border border-loss/40 bg-panel px-4 py-3 text-sm text-loss"
          role="alert"
        >
          {error}
        </p>
      )}

      <Card title="Nova carteira de cliente" className="mb-6">
        <form onSubmit={create} className="flex flex-wrap items-center gap-2">
          <input
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            maxLength={80}
            placeholder="Nome do cliente"
            aria-label="Nome do cliente"
            className="min-w-0 flex-1 rounded-md border border-hairline bg-paper px-3 py-2 text-sm text-ink placeholder:text-muted focus:border-navy focus:outline-none"
          />
          <button
            type="submit"
            disabled={write.isPending || !label.trim()}
            className="rounded-md bg-navy px-4 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-navy/90 disabled:opacity-60"
          >
            Adicionar
          </button>
        </form>
      </Card>

      {isLoading ? null : managed.length === 0 ? (
        <EmptyState
          icon={<IconUsers size={28} />}
          title="Cadastre a carteira do primeiro cliente."
          hint="Cada cliente tem extratos, posições e IRPF próprios — nada se mistura com a sua carteira."
        />
      ) : (
        <Card title="Clientes">
          <ul className="divide-y divide-hairline/70">
            {managed.map((p) => (
              <li key={p.id} className="flex flex-wrap items-center gap-2 py-2 first:pt-0 last:pb-0">
                {/* The label is the field: an editable list needs no edit mode.
                    Enter blurs, and blur is what saves. */}
                <input
                  defaultValue={p.label}
                  maxLength={80}
                  aria-label={`Nome da carteira ${p.label}`}
                  onBlur={(e) => rename(p, e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && e.currentTarget.blur()}
                  className="min-w-0 flex-1 rounded-md border border-hairline bg-paper px-3 py-2 text-sm text-ink focus:border-navy focus:outline-none"
                />
                <button
                  type="button"
                  onClick={() => open(p)}
                  className="rounded-md border border-hairline px-3 py-2 text-xs font-semibold text-navy transition-colors hover:bg-hairline/40"
                >
                  Abrir
                </button>
                <button
                  type="button"
                  onClick={() => remove(p)}
                  aria-label={`Excluir ${p.label}`}
                  title={`Excluir ${p.label}`}
                  className="rounded-md p-1.5 text-muted transition-colors hover:text-loss"
                >
                  <IconTrash size={16} />
                </button>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </>
  );
}
