"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { REFRESH_EVERY_MS, clearToken, getToken, refreshSession } from "@/lib/api";
import { useHealth, useMarketStatus, useMe } from "@/lib/hooks";
import { formatDate } from "@/lib/format";
import { isPublicRoute } from "@/lib/routes";
import { Brand } from "@/components/Brand";
import { Paywall } from "@/components/Paywall";
import { PortfolioSwitcher } from "@/components/PortfolioSwitcher";
import {
  IconCard,
  IconChart,
  IconClock,
  IconCompass,
  IconClose,
  IconLogout,
  IconMenu,
  IconReceipt,
  IconScissors,
  IconShield,
  IconUpload,
  IconUsers,
  IconWallet,
} from "@/components/icons";

const NAV = [
  { href: "/portfolio", label: "Portfólio", Icon: IconWallet },
  { href: "/carteiras", label: "Carteiras", Icon: IconUsers },
  { href: "/imports", label: "Importações", Icon: IconUpload },
  { href: "/estrategia", label: "Estratégia", Icon: IconCompass },
  { href: "/simulacao", label: "Simulação", Icon: IconClock },
  { href: "/irpf", label: "IRPF", Icon: IconReceipt },
  { href: "/tlh", label: "TLH", Icon: IconScissors },
  { href: "/bench", label: "Bench", Icon: IconChart },
  { href: "/assinatura", label: "Assinatura", Icon: IconCard },
  { href: "/conta", label: "Meus dados", Icon: IconShield },
];

// The bottom bar on a phone. Ten destinations behind a hamburger is a website's
// answer; an app puts the loop it is used for on screen — import, read the book,
// file it — and leaves the rest one tap deeper. Four plus "Mais", because a
// fifth tab at 375px is a 68px target with a clipped label.
// Filtered out of NAV rather than rebuilt, so a tab can never drift out of sync
// with the drawer entry of the same name — and so renaming a route drops the tab
// instead of putting `undefined` in the bar.
const TAB_HREFS = ["/portfolio", "/imports", "/irpf", "/bench"];
const TABS = NAV.filter((n) => TAB_HREFS.includes(n.href));

const isActive = (pathname: string, href: string) =>
  pathname === href || pathname.startsWith(href + "/");

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();

  // Public pages: no sidebar, no login guard. See lib/routes.ts — the list is
  // an auth decision, so it is tested.
  if (isPublicRoute(pathname)) return <>{children}</>;

  return <ShellChrome pathname={pathname}>{children}</ShellChrome>;
}

function ShellChrome({
  pathname,
  children,
}: {
  pathname: string;
  children: React.ReactNode;
}) {
  const router = useRouter();
  const [open, setOpen] = useState(false);

  useEffect(() => {
    // Carry the destination so a landing "Assinar" click survives the login bounce.
    if (!getToken()) router.replace(`/login?next=${encodeURIComponent(pathname)}`);
  }, [router, pathname]);

  // Keep the session alive for as long as the app is being used. Without this the
  // token's TTL is a wall an open tab walks into: the page is up, the user is
  // doing nothing wrong, and the next request 401s them back to /login.
  //
  // Three triggers, all funnelled through the same throttle in refreshSession, so
  // this is at most one small POST an hour however often they fire:
  //   mount    — the common case, a tab reopened after a while;
  //   focus    — a laptop that slept with the tab open and woke up hours later,
  //              where the interval below did not tick;
  //   interval — a tab left in the foreground all day.
  useEffect(() => {
    const renew = () => void refreshSession();
    renew();
    window.addEventListener("focus", renew);
    const id = window.setInterval(renew, REFRESH_EVERY_MS);
    return () => {
      window.removeEventListener("focus", renew);
      window.clearInterval(id);
    };
  }, []);

  // Drawer is transient: any navigation or Escape closes it.
  //
  // Adjusted during render rather than in an effect. This is React's documented
  // "resetting state when a prop changes" pattern: the drawer closes in the same
  // pass that navigates, so it is never painted open over the new route, and
  // there is no second render to schedule. An effect would do both.
  const [lastPath, setLastPath] = useState(pathname);
  if (lastPath !== pathname) {
    setLastPath(pathname);
    setOpen(false);
  }

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // The drawer covers the page, so the page must not scroll under it — the
  // "scroll the backdrop instead of the sheet" bug that no native drawer has.
  useEffect(() => {
    if (!open) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, [open]);

  return (
    <div className="flex min-h-screen">
      {open && (
        <button
          type="button"
          aria-label="Fechar menu"
          onClick={() => setOpen(false)}
          className="fixed inset-0 z-40 bg-ink/40 md:hidden"
        />
      )}

      <aside
        id="app-nav"
        // `invisible` when closed, not just translated off-screen: an off-screen
        // drawer is still in the tab order and still read aloud, so on a phone
        // every route announced ten links nobody could see. visibility is
        // discretely animatable, so the slide still plays.
        className={`fixed inset-y-0 left-0 z-50 flex w-[17rem] max-w-[85vw] flex-col overflow-y-auto overscroll-contain border-r border-hairline bg-panel pt-safe pb-safe shadow-2xl transition-[transform,visibility] duration-300 ease-out md:visible md:w-60 md:max-w-none md:translate-x-0 md:shadow-none print:hidden ${
          open ? "visible translate-x-0" : "invisible -translate-x-full"
        }`}
      >
        <div className="flex items-center justify-between px-5 py-4 md:py-5">
          <Link href="/portfolio" className="block">
            <Brand className="text-lg" />
          </Link>
          <button
            type="button"
            onClick={() => setOpen(false)}
            aria-label="Fechar menu"
            className="-mr-2 flex h-11 w-11 items-center justify-center rounded-full text-muted active:bg-hairline/50 hover:text-ink md:hidden"
          >
            <IconClose size={20} />
          </button>
        </div>

        <PortfolioSwitcher />

        <nav className="flex-1 px-3">
          <ul className="space-y-0.5">
            {NAV.map(({ href, label, Icon }) => (
              <li key={href}>
                <Link
                  href={href}
                  aria-current={isActive(pathname, href) ? "page" : undefined}
                  // min-h-11: 44px, the smallest target a thumb hits reliably.
                  // The desktop row stays at its original density.
                  className={`flex min-h-11 items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors md:min-h-0 ${
                    isActive(pathname, href)
                      ? "bg-navy/[0.06] text-navy"
                      : "text-muted active:bg-hairline/60 hover:bg-hairline/40 hover:text-ink"
                  }`}
                >
                  <Icon size={18} />
                  {label}
                </Link>
              </li>
            ))}
          </ul>
        </nav>

        <SidebarFooter />
      </aside>

      {/* print:* — "Baixar PDF" on the IRPF report is the browser's own
          print-to-PDF over the already-rendered report, so the sidebar's gutter
          has to collapse or every page carries a 15rem blank margin.
          pb-tabbar reserves the bottom bar's height so the last row of a list is
          not sitting under it. */}
      <main className="min-w-0 flex-1 px-4 pb-tabbar md:ml-60 md:px-8 md:py-8 md:pb-8 print:m-0 print:p-0">
        {/* Translucent and blurred, like every native title bar: content scrolls
            *under* it rather than disappearing at a hard edge. pt-safe puts it
            below the notch, which `viewportFit: "cover"` let it paint into. */}
        <div className="sticky top-0 z-30 -mx-4 mb-4 flex items-center gap-1 border-b border-hairline bg-paper/85 px-2 pt-safe backdrop-blur-md md:hidden print:hidden">
          <button
            type="button"
            onClick={() => setOpen(true)}
            aria-label="Abrir menu"
            aria-expanded={open}
            aria-controls="app-nav"
            className="flex h-14 w-11 items-center justify-center rounded-md text-ink active:bg-hairline/50"
          >
            <IconMenu size={22} />
          </button>
          <Brand className="text-base" />
        </div>
        <div className="mx-auto max-w-6xl">
          <MarketStaleNotice />
          {children}
        </div>
      </main>

      <MobileTabs pathname={pathname} onMore={() => setOpen(true)} open={open} />

      <Paywall />
    </div>
  );
}

/** The bottom tab bar — phones only. Four destinations plus the drawer.
 *
 *  Fixed to the bottom rather than riding the page, because that is what makes
 *  a thumb able to navigate without scrolling back to a header first, and it is
 *  the one piece of chrome that most separates an app from a page. `pb-safe`
 *  keeps the labels off iOS's home indicator; `main` reserves the height with
 *  `pb-tabbar`, so nothing ends up underneath it. */
function MobileTabs({
  pathname,
  onMore,
  open,
}: {
  pathname: string;
  onMore: () => void;
  open: boolean;
}) {
  const item =
    "flex h-full min-w-0 flex-col items-center justify-center gap-1 px-1 text-[10px] font-medium leading-none transition-colors";

  return (
    <nav
      aria-label="Navegação principal"
      className="fixed inset-x-0 bottom-0 z-30 border-t border-hairline bg-paper/90 pb-safe backdrop-blur-md md:hidden print:hidden"
    >
      <ul className="grid h-16 grid-cols-5">
        {TABS.map(({ href, label, Icon }) => {
          const active = isActive(pathname, href);
          return (
            <li key={href} className="min-w-0">
              <Link
                href={href}
                aria-current={active ? "page" : undefined}
                className={`${item} ${
                  active ? "text-navy" : "text-muted active:text-ink"
                }`}
              >
                <Icon size={20} />
                <span className="w-full truncate text-center">{label}</span>
              </Link>
            </li>
          );
        })}
        <li className="min-w-0">
          <button
            type="button"
            onClick={onMore}
            aria-expanded={open}
            aria-controls="app-nav"
            className={`${item} w-full text-muted active:text-ink`}
          >
            <IconMenu size={20} />
            <span className="w-full truncate text-center">Mais</span>
          </button>
        </li>
      </ul>
    </nav>
  );
}

/** Positions, P/L, TLH and the rebalance advisor are every one of them marked
 *  from the shared price plane. When it stops updating those numbers silently go
 *  stale — this plane once sat seven months behind while the UI presented its
 *  marks as current. Rendered in AppShell so it covers every page that shows a
 *  value, rather than trusting each one to remember.
 *
 *  Not dismissible: the threshold already absorbs a long weekend plus a feriado,
 *  so if it is showing, the numbers on screen really are wrong. */
function MarketStaleNotice() {
  const market = useMarketStatus();
  if (!market.data?.stale) return null;

  const age = market.data.prices_age_days;
  const upTo = formatDate(market.data.prices.max_date);

  return (
    <div
      role="status"
      className="mb-4 rounded-md border border-status-processing/40 bg-status-processing/10 px-3 py-2 text-xs text-ink"
    >
      <strong className="font-semibold">Cotações desatualizadas.</strong>{" "}
      {age === null
        ? "Ainda não há preços carregados, então posições e P/L aparecem só a custo."
        : `O último fechamento disponível é de ${upTo} (${age} dias). Valores de mercado, P/L, IRPF e rebalanceamento usam esses preços.`}
    </div>
  );
}

function SidebarFooter() {
  const router = useRouter();
  const qc = useQueryClient();
  const me = useMe();
  const health = useHealth();
  const market = useMarketStatus();

  const apiOk = health.data?.status === "ok";
  const fresh = market.data?.prices.max_date ?? null;
  // The date alone reads as a fact either way; amber is what makes an old one
  // look wrong at a glance.
  const marketStale = market.data?.stale ?? false;

  function logout() {
    clearToken();
    qc.clear();
    router.replace("/login");
  }

  return (
    <div className="border-t border-hairline px-4 py-3">
      <div className="mb-2 flex items-center gap-2 text-xs">
        <span
          className={`inline-block h-2 w-2 rounded-full ${
            apiOk ? "bg-status-done" : "bg-status-error"
          }`}
          aria-hidden
        />
        <span className="text-muted">
          API {apiOk ? "no ar" : "indisponível"}
        </span>
      </div>
      <p
        className={`mb-3 font-mono text-[11px] ${
          marketStale ? "text-status-processing" : "text-muted"
        }`}
        title={marketStale ? "Cotações desatualizadas" : undefined}
      >
        Mercado até {formatDate(fresh)}
      </p>

      <div className="flex items-center justify-between gap-2">
        <span className="min-w-0 truncate text-xs text-ink" title={me.data?.email}>
          {me.data?.email ?? "—"}
        </span>
        <button
          type="button"
          onClick={logout}
          className="inline-flex items-center gap-1 rounded-md px-1.5 py-1 text-xs text-muted hover:text-ink"
          title="Sair"
        >
          <IconLogout size={15} />
          sair
        </button>
      </div>
    </div>
  );
}
