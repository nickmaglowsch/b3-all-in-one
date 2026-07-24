"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { clearToken, getToken } from "@/lib/api";
import { useHealth, useMarketStatus, useMe } from "@/lib/hooks";
import { formatDate } from "@/lib/format";
import { Paywall } from "@/components/Paywall";
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
  IconUpload,
  IconWallet,
} from "@/components/icons";

const NAV = [
  { href: "/portfolio", label: "Portfólio", Icon: IconWallet },
  { href: "/imports", label: "Importações", Icon: IconUpload },
  { href: "/estrategia", label: "Estratégia", Icon: IconCompass },
  { href: "/simulacao", label: "Simulação", Icon: IconClock },
  { href: "/irpf", label: "IRPF", Icon: IconReceipt },
  { href: "/tlh", label: "TLH", Icon: IconScissors },
  { href: "/bench", label: "Bench", Icon: IconChart },
  { href: "/assinatura", label: "Assinatura", Icon: IconCard },
];

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();

  // Landing and login stand alone — no sidebar, no guard.
  if (pathname === "/" || pathname === "/login") return <>{children}</>;

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
  useEffect(() => setOpen(false), [pathname]);
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
        className={`fixed inset-y-0 left-0 z-40 flex w-60 flex-col border-r border-hairline bg-panel transition-transform md:translate-x-0 ${
          open ? "translate-x-0" : "-translate-x-full"
        }`}
      >
        <div className="flex items-center justify-between px-5 py-5">
          <Link href="/portfolio" className="block">
            <span className="font-display text-lg font-extrabold tracking-tight text-navy">
              B3
            </span>
            <span className="font-display text-lg font-bold tracking-tight text-ink">
              {" · tudo-em-um"}
            </span>
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

      <main className="min-w-0 flex-1 px-4 pb-8 md:ml-60 md:px-8 md:py-8">
        <div className="sticky top-0 z-20 -mx-4 mb-4 flex h-14 items-center gap-2 border-b border-hairline bg-paper px-4 md:hidden">
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
          <span className="font-display text-base font-extrabold tracking-tight text-navy">
            B3
          </span>
          <span className="font-display text-base font-bold tracking-tight text-ink">
            · tudo-em-um
          </span>
        </div>
        <div className="mx-auto max-w-6xl">{children}</div>
      </main>

      <Paywall />
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
      <p className="mb-3 font-mono text-[11px] text-muted">
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
