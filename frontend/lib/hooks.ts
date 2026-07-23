"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "./api";
import type { Health, MarketStatus, Me } from "./types";

export function useMe() {
  return useQuery({ queryKey: ["me"], queryFn: () => api<Me>("/me") });
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
