"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "./api";
import type { Health, MarketStatus, Me, Subscription } from "./types";

export function useMe() {
  return useQuery({ queryKey: ["me"], queryFn: () => api<Me>("/me") });
}

export function useSubscription(awaitingCheckout = false) {
  return useQuery({
    queryKey: ["subscription"],
    queryFn: () => api<Subscription>("/billing"),
    // Coming back from Stripe, the plan only flips once the webhook lands — poll a
    // few times instead of making the user reload. Capped so an abandoned checkout
    // doesn't poll forever.
    refetchInterval: (q) =>
      awaitingCheckout &&
      q.state.data?.plan === "gratis" &&
      q.state.dataUpdateCount < 12
        ? 2000
        : false,
  });
}

export function useHealth() {
  return useQuery({
    queryKey: ["health"],
    queryFn: () => api<Health>("/health"),
    refetchInterval: 60_000,
  });
}

export function useMarketStatus() {
  return useQuery({
    queryKey: ["market-status"],
    queryFn: () => api<MarketStatus>("/market/status"),
    refetchInterval: 60_000,
  });
}
