"use client";

// Shared data layer for user-created strategies (spec/06-user-strategies.md),
// used by both builders: the rule builder on /bench and the portfolio builder
// on /estrategia. Page-specific form state stays in each page's own dir.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./api";
import type { SaveStrategyResult, SignalDef, StrategyKind, UserStrategy } from "./types";

// The named-signal catalog feeds the factor picker. Baked into the image, so it
// only changes on rebuild — cache long like the gallery.
export function useSignals() {
  return useQuery({
    queryKey: ["signals"],
    queryFn: () => api<SignalDef[]>("/strategies/signals"),
    staleTime: 60 * 60_000,
  });
}

export function useUserStrategies() {
  return useQuery({
    queryKey: ["user-strategies"],
    // On load in both builders: a 402 must not open a modal nobody asked for.
    queryFn: () => api<UserStrategy[]>("/strategies/user", {}, { paywallDialog: false }),
  });
}

// Raw spec of a config (spec-kind) strategy by name — for cloning a built-in
// into user space with its full factor/universe/selection, not just the exposed
// numeric params. 404 for code-kind strategies (no spec); `enabled` gates it to
// an active clone. GET /strategies/{name}/spec → {name, kind, spec}.
export function useStrategySpec(name: string | null) {
  return useQuery({
    queryKey: ["strategy-spec", name],
    enabled: !!name,
    staleTime: 60 * 60_000,
    queryFn: () =>
      api<{ name: string; kind: StrategyKind; spec: Record<string, unknown> }>(
        `/strategies/${encodeURIComponent(name!)}/spec`,
      ),
  });
}

// Save spec is the compiled request body each builder assembles (rank or
// portfolio). POST when no id, PUT to update an existing row. Invalidates the
// merged gallery too, since a new strategy shows up there.
export function useSaveUserStrategy() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id?: string; body: Record<string, unknown> }) =>
      api<SaveStrategyResult>(id ? `/strategies/user/${id}` : "/strategies/user", {
        method: id ? "PUT" : "POST",
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["user-strategies"] });
      qc.invalidateQueries({ queryKey: ["strategies"] });
    },
  });
}

// `onDeleted` is hook-level on purpose: a callback passed to mutate() fires only
// for the latest call, and a screen holding an edit of the deleted strategy must
// hear about every delete, not just the last one.
export function useDeleteUserStrategy(onDeleted?: (id: string) => void) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      api<{ deleted: string }>(`/strategies/user/${id}`, { method: "DELETE" }),
    onSuccess: (_r, id) => {
      qc.invalidateQueries({ queryKey: ["user-strategies"] });
      qc.invalidateQueries({ queryKey: ["strategies"] });
      onDeleted?.(id);
    },
  });
}

// Promote a portfolio strategy to the live rebalance target (compiles its
// sleeves through PUT /portfolio/sleeves server-side). Refresh the target views.
export function usePromoteStrategy() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      api<{ sleeves: unknown[]; warnings: string[] }>(
        `/strategies/user/${id}/promote`,
        { method: "POST" },
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["sleeves"] });
      qc.invalidateQueries({ queryKey: ["target"] });
    },
  });
}
