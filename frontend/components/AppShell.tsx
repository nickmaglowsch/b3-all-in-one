"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { clearToken, getToken } from "@/lib/api";
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

  return (
    <div className="flex min-h-screen">
      {open && (
        <button
          type="button"
          aria-label="Fechar menu"
          onClick={() => setOpen(false)}
          className="fixed inset-0 z-30 bg-ink/25 md:hidden"
        />
      )}

      <aside
        id="app-nav"
        className={`fixed inset-y-0 left-0 z-40 flex w-60 flex-col border-r border-hairline bg-panel transition-transform md:translate-x-0 print:hidden ${
          open ? "translate-x-0" : "-translate-x-full"
        }`}
      >
        <div className="flex items-center justify-between px-5 py-5">
          <Link href="/portfolio" className="block">
            <Brand className="text-lg" />
          </Link>
          <button
            type="button"
            onClick={() => setOpen(false)}
            aria-label="Fechar menu"
            className="-mr-1 rounded-md p-1 text-muted hover:text-ink md:hidden"
          >
            <IconClose size={18} />
          </button>
        </div>

        <PortfolioSwitcher />

        <nav className="flex-1 px-3">
          <ul className="space-y-0.5">
            {NAV.map(({ href, label, Icon }) => {
              const active =
                pathname === href || pathname.startsWith(href + "/");
              return (
                <li key={href}>
                  <Link
                    href={href}
                    aria-current={active ? "page" : undefined}
                    className={`flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors ${
                      active
                        ? "bg-navy/[0.06] text-navy"
                        : "text-muted hover:bg-hairline/40 hover:text-ink"
                    }`}
                  >
                    <Icon size={18} />
                    {label}
                  </Link>
                </li>
              );
            })}
          </ul>
        </nav>

        <SidebarFooter />
      </aside>

      {/* print:* — "Baixar PDF" on the IRPF report is the browser's own
          print-to-PDF over the already-rendered report, so the sidebar's gutter
          has to collapse or every page carries a 15rem blank margin. */}
      <main className="min-w-0 flex-1 px-4 pb-8 md:ml-60 md:px-8 md:py-8 print:m-0 print:p-0">
        <div className="sticky top-0 z-20 -mx-4 mb-4 flex h-14 items-center gap-2 border-b border-hairline bg-paper px-4 md:hidden print:hidden">
          <button
            type="button"
            onClick={() => setOpen(true)}
            aria-label="Abrir menu"
            aria-expanded={open}
            aria-controls="app-nav"
            className="-ml-1 rounded-md p-1.5 text-ink hover:bg-hairline/40"
          >
            <IconMenu size={20} />
          </button>
          <Brand className="text-base" />
        </div>
        <div className="mx-auto max-w-6xl">
          <MarketStaleNotice />
          {children}
        </div>
      </main>

      <Paywall />
    </div>
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
