"use client";

import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { usePathname } from "next/navigation";
import { TAB_PARAM, pickTab, stepTab } from "@/lib/tabs";

/**
 * Real tabs: one panel on screen at a time.
 *
 * These pages used to stack every section and put a strip of anchor links on
 * top (SectionNav). That strip looked like tabs and behaved like a table of
 * contents — a click scrolled you thousands of pixels down a page you still had
 * to scroll through, and the portfolio ran 7,000px on a desktop and 36,000px on
 * a phone. Here a click swaps the panel and the page stays short.
 *
 * Panels mount the first time they are opened and then stay mounted (hidden),
 * so a tab's own state — a trades page, a filter, a plan just computed — is
 * still there when you come back, and an unopened tab costs no request.
 */

export interface TabDef {
  id: string;
  label: string;
  /** A count or marker after the label (e.g. runs picked for comparison). */
  badge?: React.ReactNode;
  content: React.ReactNode;
}

// The URL is the store: `?aba=` makes a tab linkable and keeps it across a
// reload. useSyncExternalStore rather than reading `window` in a state
// initialiser, because the static export prerenders without a URL — the server
// snapshot (null → first tab) hydrates cleanly and the client one follows.
const listeners = new Set<() => void>();
function subscribe(onChange: () => void) {
  listeners.add(onChange);
  window.addEventListener("popstate", onChange);
  return () => {
    listeners.delete(onChange);
    window.removeEventListener("popstate", onChange);
  };
}

/** The active tab of `ids`, read from and written to `?aba=`. */
export function useTab(ids: string[], param = TAB_PARAM) {
  const requested = useSyncExternalStore(
    subscribe,
    () => new URLSearchParams(window.location.search).get(param),
    () => null,
  );
  const active = pickTab(ids, requested);
  const first = ids[0];
  const select = useCallback(
    (id: string) => {
      const url = new URL(window.location.href);
      // The default tab leaves no param behind: a plain link to the page should
      // stay a plain link.
      if (id === first) url.searchParams.delete(param);
      else url.searchParams.set(param, id);
      // `null`, not `history.state`: Next's patched replaceState treats a state
      // carrying its own marker as an internal call and skips syncing the
      // router, so useSearchParams would never see `?aba=`.
      window.history.replaceState(null, "", url);
      listeners.forEach((l) => l());
    },
    [first, param],
  );
  // A `?aba=` naming a tab that is not on the strip (a bookmark, a book switch
  // that kept the URL) is rewritten to the tab actually shown. Left in place it
  // would fire later: the moment that tab appeared — sleeves saved, positions
  // imported — the page would jump to it on its own and hide what the reader
  // had just done. Callers pass only the ids they render.
  //
  // Decided from the URL as it is when the effect runs, never from the value
  // read during render, and only on this hook's own page:
  //  - In a client-side navigation Next renders the incoming page before it
  //    pushes the new URL, so render time sees the *outgoing* page's `?aba=`.
  //    Trusting it stripped a valid `?aba=` off every link that carried one.
  //  - Back/forward is the opposite: the URL changes while the page being left
  //    is still mounted and hears popstate, and Bench would read Portfólio's
  //    `?aba=posicoes` as a stray of its own. The pathname check stops that.
  const pathname = usePathname();
  const key = ids.join(",");
  useEffect(() => {
    if (window.location.pathname !== pathname) return;
    const now = new URLSearchParams(window.location.search).get(param);
    const shown = key.split(",");
    if (now !== null && !shown.includes(now)) select(shown[0]);
  }, [requested, key, param, pathname, select]);
  return [active, select] as const;
}

export function Tabs({
  label,
  tabs,
  active: requested,
  onChange,
  trailing,
}: {
  /** Accessible name of the tablist. */
  label: string;
  tabs: TabDef[];
  active: string;
  onChange: (id: string) => void;
  /** Right-aligned beside the tabs — the client book's name, for instance. */
  trailing?: React.ReactNode;
}) {
  const ids = tabs.map((t) => t.id);
  // The caller's id may name a tab that is not rendered right now — a
  // bookmarked `?aba=alvo` on a book with no sleeves. Without this fallback no
  // tab was selected, no panel shown and the tablist had no tab stop at all.
  const active = pickTab(ids, requested);
  const top = useRef<HTMLDivElement>(null);
  const mounted = useRef(false);
  const list = useRef<HTMLDivElement>(null);

  // Mount-on-first-visit, adjusted during render (React's "derive state from a
  // prop" pattern) so the newly opened panel paints in the same pass.
  const [seen, setSeen] = useState<Set<string>>(() => new Set([active]));
  if (!seen.has(active)) setSeen(new Set(seen).add(active));

  // On every change of tab, whoever made it — a click here, or the page
  // switching tabs itself (Bench sends a queued run to Rodadas):
  //  - Six tabs do not fit across 375px, so the strip scrolls; keep the active
  //    one inside it. `block: "nearest"` only ever scrolls the strip.
  //  - The strip is sticky, so switching from deep inside a long panel would
  //    leave the new one starting above the fold. Bring its top back into
  //    view — only when we are past it, and never on first mount, where the
  //    browser's own scroll restoration is in charge.
  useEffect(() => {
    list.current
      ?.querySelector(`[data-tab="${active}"]`)
      ?.scrollIntoView({ inline: "nearest", block: "nearest" });
    const el = top.current;
    if (mounted.current && el && el.getBoundingClientRect().top < 0) {
      el.scrollIntoView({ block: "start" });
    }
    mounted.current = true;
  }, [active]);

  const go = (id: string, focus = false) => {
    onChange(id);
    if (focus) list.current?.querySelector<HTMLElement>(`[data-tab="${id}"]`)?.focus();
  };

  const onKey = (e: React.KeyboardEvent) => {
    const moves: Record<string, () => string> = {
      ArrowRight: () => stepTab(ids, active, 1),
      ArrowLeft: () => stepTab(ids, active, -1),
      Home: () => ids[0],
      End: () => ids[ids.length - 1],
    };
    const move = moves[e.key];
    if (!move) return;
    e.preventDefault();
    go(move(), true);
  };

  return (
    <>
      <div ref={top} aria-hidden className="scroll-mt-14 md:scroll-mt-0" />
      {/* Full-bleed on a phone like a native segmented strip: `-mx-4 px-4`
          cancels main's gutter and gives it back inside the scroller. */}
      <div className="sticky top-[calc(3.5rem+env(safe-area-inset-top,0px))] z-20 -mx-4 mb-4 flex flex-wrap items-end gap-x-3 border-b border-hairline bg-paper/90 px-4 backdrop-blur-md md:top-0 md:mx-0 md:px-0 md:backdrop-blur-none print:hidden">
        <div
          ref={list}
          role="tablist"
          aria-label={label}
          onKeyDown={onKey}
          className="no-scrollbar flex min-w-0 flex-1 overflow-x-auto overscroll-x-contain"
        >
          {tabs.map((t) => {
            const on = t.id === active;
            return (
              <button
                key={t.id}
                type="button"
                role="tab"
                id={`tab-${t.id}`}
                data-tab={t.id}
                aria-selected={on}
                aria-controls={`panel-${t.id}`}
                tabIndex={on ? 0 : -1}
                onClick={() => go(t.id)}
                className={`-mb-px flex shrink-0 items-center gap-1.5 whitespace-nowrap border-b-2 px-3 py-2.5 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-navy ${
                  on
                    ? "border-navy text-navy"
                    : "border-transparent text-muted hover:border-axis hover:text-ink"
                }`}
              >
                {t.label}
                {t.badge != null && (
                  <span className="rounded-full bg-navy/[0.08] px-1.5 py-px font-mono text-[10px] text-navy">
                    {t.badge}
                  </span>
                )}
              </button>
            );
          })}
        </div>
        {/* Beside the tabs from `sm`; on a phone its own row above them (below
            would part the active underline from the strip's rule), so the
            client's name (or the alarm that it is missing) is in every
            screenshot of every tab, at any width. `empty:hidden` because the
            slot is an element that may render nothing (the user's own book). */}
        {trailing && (
          <div className="order-first basis-full pt-2 empty:hidden sm:order-none sm:basis-auto sm:shrink-0 sm:pb-2.5 sm:pt-0">
            {trailing}
          </div>
        )}
      </div>

      {tabs.map(
        (t) =>
          seen.has(t.id) && (
            <div
              key={t.id}
              role="tabpanel"
              id={`panel-${t.id}`}
              aria-labelledby={`tab-${t.id}`}
              hidden={t.id !== active}
              className="focus-visible:outline-none"
            >
              {t.content}
            </div>
          ),
      )}
    </>
  );
}
