"use client";

// "Conectar uma IA" (/bench): mints and revokes the tokens an MCP client uses
// to drive the bench (spec/09). Whatever the AI saves lands in "Minhas
// estratégias" with the IA badge and its runs in "Rodadas" — this card only
// hands out the key and the line to paste.
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { API_URL, api, errorText } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { claudeCodeCommand, mcpJsonConfig, mcpUrl } from "@/lib/mcp";
import { Card } from "@/components/ui/Card";
import { LoadFailure } from "@/components/ui/EmptyState";
import { Field, inputCls } from "./Field";

interface McpToken {
  id: string;
  name: string;
  prefix: string;
  created_at: string | null;
  last_used_at: string | null;
}

const KEY = ["mcp-tokens"];

function useMcpTokens() {
  return useQuery({
    queryKey: KEY,
    queryFn: () => api<McpToken[]>("/mcp/tokens", {}, { paywallDialog: false }),
  });
}

function useCreateToken() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) =>
      api<McpToken & { token: string }>("/mcp/tokens", {
        method: "POST",
        body: JSON.stringify({ name }),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
  });
}

function useRevokeToken() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      api<{ deleted: string }>(`/mcp/tokens/${id}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
  });
}

export function McpConnect() {
  const tokens = useMcpTokens();
  const [created, setCreated] = useState<string | null>(null);

  return (
    <Card title="Conectar uma IA (MCP)">
      <p className="mb-3 max-w-prose text-sm leading-relaxed text-muted">
        Dê a um assistente de IA — Claude ou qualquer cliente MCP — acesso a este
        Bench. Ele lê a linguagem de estratégias, cria variações e roda backtests na
        sua conta. O que ele salvar aparece em{" "}
        <span className="font-medium text-ink">Minhas estratégias</span> com o selo{" "}
        <span className="font-mono text-[11px] text-navy">IA</span>, e cada rodada
        em <span className="font-medium text-ink">Rodadas</span>.
      </p>
      {tokens.isError ? (
        <LoadFailure
          error={tokens.error}
          title="Não foi possível carregar seus tokens."
          offerTitle={(nome) => `A conexão com IA vem com o módulo ${nome}.`}
          onRetry={() => tokens.refetch()}
        />
      ) : created ? (
        <CreatedToken token={created} onDone={() => setCreated(null)} />
      ) : (
        <NewTokenForm onCreated={setCreated} />
      )}
      <TokenList tokens={tokens.data ?? []} />
    </Card>
  );
}

function NewTokenForm({ onCreated }: { onCreated: (token: string) => void }) {
  const create = useCreateToken();
  const [name, setName] = useState("Claude Code");

  return (
    <form
      className="flex flex-wrap items-end gap-2"
      onSubmit={(e) => {
        e.preventDefault();
        create.mutate(name.trim(), { onSuccess: (r) => onCreated(r.token) });
      }}
    >
      <Field label="Nome do token" hint="para reconhecê-lo depois" className="min-w-48 flex-1">
        <input value={name} onChange={(e) => setName(e.target.value)} className={inputCls} />
      </Field>
      <button
        type="submit"
        disabled={create.isPending || !name.trim()}
        className="rounded-md bg-navy px-3 py-2 text-xs font-semibold text-white hover:bg-navy/90 disabled:opacity-60"
      >
        Gerar token
      </button>
      {create.error && (
        <p className="w-full text-sm text-loss" role="alert">
          {errorText(create.error, "Falha ao gerar o token.")}
        </p>
      )}
    </form>
  );
}

function CreatedToken({ token, onDone }: { token: string; onDone: () => void }) {
  const url = mcpUrl(API_URL);
  return (
    <div className="space-y-3 rounded-md border border-status-processing/40 bg-status-processing/10 p-3">
      <p className="text-sm font-medium text-ink">
        Copie agora — este token não será mostrado de novo.
      </p>
      <Snippet label="Token" text={token} />
      <Snippet label="Claude Code (terminal)" text={claudeCodeCommand(url, token)} />
      <Snippet label="Outros clientes (JSON)" text={mcpJsonConfig(url, token)} />
      <p className="text-xs text-muted">
        Peça, por exemplo: “Leia o guia de autoria e procure uma estratégia de momentum
        na B3 que bata a MomSharpe depois do imposto, validando em duas janelas.”
      </p>
      <button
        type="button"
        onClick={onDone}
        className="rounded-md border border-hairline bg-paper px-3 py-1.5 text-xs font-medium text-ink hover:bg-hairline/40"
      >
        Pronto, copiei
      </button>
    </div>
  );
}

function Snippet({ label, text }: { label: string; text: string }) {
  const [copied, setCopied] = useState(false);
  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    } catch {
      /* clipboard blocked — the text is selectable */
    }
  }
  return (
    <div>
      <div className="mb-1 flex items-center justify-between gap-2">
        <span className="text-xs font-medium text-ink">{label}</span>
        <button
          type="button"
          onClick={copy}
          className="rounded-md border border-hairline bg-paper px-2 py-0.5 text-xs font-medium text-muted hover:text-ink"
        >
          {copied ? "Copiado" : "Copiar"}
        </button>
      </div>
      <pre className="overflow-x-auto whitespace-pre-wrap break-all rounded-md border border-hairline bg-paper px-2.5 py-2 font-mono text-[12px] text-ink">
        {text}
      </pre>
    </div>
  );
}

function TokenList({ tokens }: { tokens: McpToken[] }) {
  const revoke = useRevokeToken();
  const [confirmId, setConfirmId] = useState<string | null>(null);
  if (tokens.length === 0) return null;

  return (
    <ul className="mt-4 space-y-1.5">
      {revoke.error && (
        <li className="text-sm text-loss" role="alert">
          {errorText(revoke.error, "Falha ao revogar.")}
        </li>
      )}
      {tokens.map((t) => (
        <li
          key={t.id}
          className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-hairline bg-panel px-3 py-2"
        >
          <div className="min-w-0">
            <span className="text-sm font-semibold text-ink">{t.name}</span>{" "}
            <span className="font-mono text-[11px] text-muted">{t.prefix}…</span>
            <p className="text-xs text-muted">
              criado {formatDateTime(t.created_at)} ·{" "}
              {t.last_used_at ? `último uso ${formatDateTime(t.last_used_at)}` : "nunca usado"}
            </p>
          </div>
          <button
            type="button"
            disabled={revoke.isPending}
            onClick={() =>
              confirmId === t.id
                ? revoke.mutate(t.id, { onSettled: () => setConfirmId(null) })
                : setConfirmId(t.id)
            }
            className="rounded-md border border-loss/40 bg-paper px-2 py-1 text-xs font-medium text-loss hover:bg-loss/10 disabled:opacity-60"
          >
            {confirmId === t.id ? "Confirmar revogação" : "Revogar"}
          </button>
        </li>
      ))}
    </ul>
  );
}
