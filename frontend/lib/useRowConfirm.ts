"use client";

import { useState } from "react";
import type { UseMutationResult } from "@tanstack/react-query";
import { arm, rowState, settle, type RowState } from "./rowConfirm";

/**
 * The inline delete confirm shared by the /bench lists (runs, strategies, MCP
 * tokens). Takes the list's delete mutation — called with the row's id — and
 * answers each row's state from it; lib/rowConfirm.ts has the rules and why.
 */
export function useRowConfirm<TData, TError, TContext>(
  mutation: UseMutationResult<TData, TError, string, TContext>,
) {
  const [armed, setArmed] = useState<string | null>(null);
  const busyId = mutation.isPending ? (mutation.variables ?? null) : null;

  return {
    /** A delete is in flight: every other row's Excluir waits for it. */
    busy: busyId !== null,
    stateOf: (id: string): RowState => rowState(id, armed, busyId),
    arm: (id: string) => setArmed((a) => arm(a, id, busyId)),
    cancel: () => setArmed(null),
    confirm: (id: string, onDeleted?: (id: string) => void) =>
      mutation.mutate(id, {
        onSuccess: () => onDeleted?.(id),
        onSettled: () => setArmed((a) => settle(a, id)),
      }),
  };
}
