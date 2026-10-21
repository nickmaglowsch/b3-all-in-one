"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { isModuleRequired, type ModuleRequired, PAYWALL_EVENT } from "@/lib/api";
import { ligarHref, moduloDe } from "@/lib/modules";

/** Global answer to any 402: whatever the user clicked, they land here.
 *  Native <dialog> so focus trapping, Esc and the backdrop come from the platform. */
export function Paywall() {
  const ref = useRef<HTMLDialogElement>(null);
  const [gate, setGate] = useState<ModuleRequired | null>(null);

  useEffect(() => {
    const onPaywall = (e: Event) => {
      const detail = (e as CustomEvent).detail;
      if (isModuleRequired(detail)) setGate(detail);
    };
    window.addEventListener(PAYWALL_EVENT, onPaywall);
    return () => window.removeEventListener(PAYWALL_EVENT, onPaywall);
  }, []);

  useEffect(() => {
    if (gate) ref.current?.showModal();
  }, [gate]);

  const modulo = gate && moduloDe(gate.required_module);
  if (!gate || !modulo) return null;

  return (
    <dialog
      ref={ref}
      onClose={() => setGate(null)}
      className="m-auto w-[min(28rem,calc(100vw-2rem))] rounded-lg border border-hairline bg-panel p-0 text-ink backdrop:bg-ink/30"
    >
      <div className="p-6">
        <p className="font-mono text-[11px] font-medium uppercase tracking-[0.14em] text-muted">
          Módulo não assinado
        </p>
        <h2 className="mt-2 font-display text-xl font-extrabold tracking-tight">
          Isto vem com {modulo.name}
        </h2>
        <p className="mt-3 text-sm text-muted">{modulo.carga}</p>

        <div className="mt-5 flex items-baseline justify-between border-t border-hairline pt-4">
          <span className="text-sm">{modulo.name}, por mês</span>
          <span className="font-display text-3xl font-extrabold tracking-tight tnum">
            R$ {modulo.price}
          </span>
        </div>
        {/* O resto do carrinho continua como está: ligar um módulo não desliga outro. */}
        <p className="mt-2 text-[13px] text-muted">
          Somado ao que você já assina. Nada mais muda.
        </p>

        <div className="mt-6 flex gap-2">
          <Link
            href={ligarHref(modulo.id)}
            onClick={() => ref.current?.close()}
            className="flex-1 rounded-md bg-navy px-4 py-2.5 text-center text-sm font-semibold text-white transition-colors hover:bg-navy/90"
          >
            Ligar {modulo.name}
          </Link>
          <button
            type="button"
            onClick={() => ref.current?.close()}
            className="rounded-md border border-hairline px-4 py-2.5 text-sm font-semibold transition-colors hover:bg-hairline/40"
          >
            Agora não
          </button>
        </div>
      </div>
    </dialog>
  );
}
