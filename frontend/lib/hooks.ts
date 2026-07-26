"use client";

import { useSyncExternalStore } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, getPortfolioId, subscribePortfolio } from "./api";
import type {
  Health,
  MarketStatus,
  Me,
  Portfolio,
  PrivacySummary,
  Subscription,
} from "./types";

export function useMe() {
  return useQuery({ queryKey: ["me"], queryFn: () => api<Me>("/me") });
}

export function usePortfolios() {
  return useQuery({
    queryKey: ["portfolios"],
    queryFn: () => api<Portfolio[]>("/portfolios"),
  });
}

/**
 * Which book the page is acting on. The enterprise tier is sold on a client's
 * data never appearing under another client's name, so the surfaces that show
 * money have to say whose it is — a sidebar select behind a drawer is not an
 * answer on a printed page.
 *
 * Three states, not two. Collapsing "my own book" and "don't know yet" into one
 * null fails OPEN: `X-Portfolio-Id` is already on every request, so on a cold
 * load the positions call can beat /portfolios and paint a client's money under
 * a label meaning "mine" — and if /portfolios errors, that state is permanent
 * for the session. Unknown has to be visible, not silently generic.
 */
export type ActiveBook =
  | { state: "self" }
  | { state: "client"; label: string }
  | { state: "pending" }
  | { state: "unknown" };

export function useActiveBook(): ActiveBook {
  const { data, isError, isLoading } = usePortfolios();
  const active = useSyncExternalStore(subscribePortfolio, getPortfolioId, () => null);
  if (!active) return { state: "self" };
  // "Still loading" is not "we don't know". Collapsing them made every cold load
  // of a client book open on an alarm before settling — the standard entry frame
  // for the tier's main flow. Pending renders as absence; unknown renders loudly.
  if (isLoading || (!data && !isError)) return { state: "pending" };
  if (isError) return { state: "unknown" };
  const book = data?.find((p) => p.id === active);
  if (!book) return { state: "unknown" }; // stale id; the switcher reconciles it
  return book.is_self ? { state: "self" } : { state: "client", label: book.label };
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

// Account-scoped like /billing: it answers for every book at once, so it is not
// affected by which one is selected.
export function usePrivacySummary() {
  return useQuery({
    queryKey: ["privacy-summary"],
    queryFn: () => api<PrivacySummary>("/privacy/summary"),
  });
}

export function useHealth() {
  return useQuery({
    queryKey: ["health"],
    queryFn: () => api<Health>("/health"),
    refetchInterval: 60_000,
  });
}

// The market plane only moves when the ingestion pipeline is run, which is a
// manual job — polling it every minute asked the DB to aggregate the whole
// `prices` table 60x an hour per open tab to report a number that changes at
// most daily. The API caches this for a minute regardless; this just stops the
// client asking for what cannot have changed.
export function useMarketStatus() {
  return useQuery({
    queryKey: ["market-status"],
    queryFn: () => api<MarketStatus>("/market/status"),
    refetchInterval: 300_000,
  });
}
