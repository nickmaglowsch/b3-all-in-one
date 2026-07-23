"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { clearToken, getToken } from "@/lib/api";
import { useHealth, useMarketStatus, useMe } from "@/lib/hooks";
import { formatDate } from "@/lib/format";
import {
  IconChart,
  IconClock,
  IconCompass,
  IconLogout,
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
];

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();

  // Login stands alone — no sidebar, no guard.
  if (pathname === "/login") return <>{children}</>;

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

  useEffect(() => {
    if (!getToken()) router.replace("/login");
  }, [router]);

  return (
    <div className="flex min-h-screen">
      <aside className="fixed inset-y-0 left-0 flex w-60 flex-col border-r border-hairline bg-panel">
        <div className="px-5 py-5">
          <Link href="/portfolio" className="block">
            <span className="font-display text-lg font-extrabold tracking-tight text-navy">
              B3
            </span>
            <span className="font-display text-lg font-bold tracking-tight text-ink">
              {" · tudo-em-um"}
            </span>
          </Link>
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

      <main className="ml-60 flex-1 px-8 py-8">
        <div className="mx-auto max-w-6xl">{children}</div>
      </main>
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
