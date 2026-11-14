"use client";

import { useSyncExternalStore } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, getPortfolioId, subscribePortfolio } from "./api";
import { pollEvery, type Confirm } from "./checkoutConfirm";
import type {
  Health,
  IncomeRow,
  MarketStatus,
  Me,
  Portfolio,
  Position,
  PrivacySummary,
  Subscription,
} from "./types";

const neverChanges = () => () => {};

/**
 * A browser-only value that nothing changes while the page is open (the URL, the
 * stored credential). useSyncExternalStore rather than setState-in-an-effect:
 * `server` is the prerender's answer, which keeps the static export's HTML
 * matching the first client render; `read` is the client's. Never subscribes.
 */
export function useBrowserValue<T, S>(read: () => T, server: S): T | S {
  return useSyncExternalStore<T | S>(neverChanges, read, () => server);
}

export function useMe() {
  return useQuery({ queryKey: ["me"], queryFn: () => api<Me>("/me") });
}

// Book-scoped reads shared by several pages; one key each, so React Query serves
// every caller from one request.
export function usePositions() {
  return useQuery({
    queryKey: ["positions"],
    queryFn: () => api<Position[]>("/portfolio/positions"),
  });
}

export function useIncome() {
  return useQuery({
    queryKey: ["income"],
    queryFn: () => api<IncomeRow[]>("/portfolio/income"),
  });
}

export function usePortfolios() {
  return useQuery({
    queryKey: ["portfolios"],
    queryFn: () => api<Portfolio[]>("/portfolios"),
  });
}

/**
 * Which book the page is acting on. The empresarial module is sold on a client's
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
  const { data, isError } = usePortfolios();
  const active = useSyncExternalStore(subscribePortfolio, getPortfolioId, () => null);
  // sessionStorage does not exist at export time, so the prerendered HTML has no
  // book and would bake in "Carteira" — the label meaning "my own money" — over
  // a client's figures. Until hydration we do not know, and say so.
  const hydrated = useSyncExternalStore(
    subscribePortfolio,
    () => true,
    () => false,
  );
  if (!hydrated) return { state: "pending" };
  if (!active) return { state: "self" };

  // Answer from cache FIRST. React Query keeps `data` when a *refetch* fails, so
  // checking isError before the lookup would swap a client's name for an alarm
  // on a background blip — the same "opens on an alarm" failure this hook was
  // written to remove, just moved to a different trigger.
  const book = data?.find((p) => p.id === active);
  if (book) {
    return book.is_self ? { state: "self" } : { state: "client", label: book.label };
  }
  // No answer available. "Still loading" is absence; a real failure, or an id the
  // list does not contain, is loud — never the generic "Carteira", because the
  // request still carries X-Portfolio-Id and the money on screen is the client's.
  if (isError || data) return { state: "unknown" };
  return { state: "pending" };
}

// While /assinatura waits for the Stripe webhook (lib/checkoutConfirm.ts), poll
// until the billed set moves off the one the change was sent against — for at
// most ESPERA_MS of wall clock from that wait's start. Not a count of fetches:
// that count lives on the ["subscription"] cache entry, which /carteiras shares
// and which never resets, so a second change in one session got few polls or none.
export function useSubscription(confirm?: Confirm) {
  return useQuery({
    queryKey: ["subscription"],
    queryFn: () => api<Subscription>("/billing"),
    refetchInterval: (q) =>
      confirm ? pollEvery(confirm, q.state.data?.billed_modules, Date.now()) : false,
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
