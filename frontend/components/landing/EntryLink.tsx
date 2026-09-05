"use client";

import Link from "next/link";
import { useSyncExternalStore } from "react";
import { getToken } from "@/lib/api";
import { entryHref } from "@/lib/routes";

// The credential is written by a login and read here; nothing changes it while
// this page is open, so there is nothing to subscribe to.
const neverChanges = () => () => {};

/**
 * The landing's entry action — "Entrar", "Começar grátis" — pointed at wherever
 * the visitor actually belongs.
 *
 * Every one of them linked to /login, which is a dead end for the one visitor
 * who cannot use it: someone already signed in, sent to sign in again.
 *
 * The session lives in localStorage, so the answer is only knowable on the
 * client. useSyncExternalStore, not an effect, because the server snapshot is
 * the honest "no storage here" — this is an `output: "export"` build, so the
 * prerendered HTML says /login, which is also the right answer for the visitor
 * who has no session and a safe one for the visitor who does: the login screen
 * forwards a live session on to the app.
 */
export function EntryLink({
  className,
  children,
}: {
  className?: string;
  children: React.ReactNode;
}) {
  const hasSession = useSyncExternalStore(
    neverChanges,
    () => getToken() !== null,
    () => false,
  );

  return (
    <Link href={entryHref(hasSession)} className={className}>
      {children}
    </Link>
  );
}
