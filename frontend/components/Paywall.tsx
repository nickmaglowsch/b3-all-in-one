"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { isPlanRequired, PAYWALL_EVENT, type PlanRequired } from "@/lib/api";
import { planOf } from "@/lib/plans";
import { IconCheck } from "@/components/icons";

/** Global answer to any 402: whatever the user clicked, they land here.
 *  Native <dialog> so focus trapping, Esc and the backdrop come from the platform. */
export function Paywall() {
  const ref = useRef<HTMLDialogElement>(null);
  const [gate, setGate] = useState<PlanRequired | null>(null);

  useEffect(() => {
    const onPaywall = (e: Event) => {
      const detail = (e as CustomEvent).detail;
      if (isPlanRequired(detail)) setGate(detail);
    };
    window.addEventListener(PAYWALL_EVENT, onPaywall);
    return () => window.removeEventListener(PAYWALL_EVENT, onPaywall);
  }, []);

  useEffect(() => {
    if (gate) ref.current?.showModal();
  }, [gate]);

  const plan = gate && planOf(gate.required_plan);
  if (!gate || !plan) return null;

  return (
    <dialog
      ref={ref}
      onClose={() => setGate(null)}
      className="m-auto w-[min(28rem,calc(100vw-2rem))] rounded-lg border border-hairline bg-panel p-0 text-ink backdrop:bg-ink/30"
    >
      <div className="p-6">
        <p className="font-mono text-[11px] font-medium uppercase tracking-[0.14em] text-muted">
          Plano {plan.name}
        </p>
        <h2 className="mt-2 font-display text-xl font-extrabold tracking-tight">
          Este recurso é do plano {plan.name}
        </h2>
        <p className="mt-2 text-sm text-muted">{plan.tagline}</p>

        <ul className="mt-5 space-y-2.5">
          {plan.features.map((f) => (
            <li key={f} className="flex items-start gap-2.5 text-sm">
              <IconCheck size={18} className="mt-0.5 shrink-0 text-navy" />
              <span>{f}</span>
            </li>
          ))}
        </ul>

        <p className="mt-5 flex items-baseline gap-1">
          <span className="font-display text-3xl font-extrabold tracking-tight tnum">
            {plan.price}
          </span>
          <span className="text-sm text-muted">{plan.cadence}</span>
        </p>

        <div className="mt-6 flex gap-2">
          <Link
            href="/assinatura"
            onClick={() => ref.current?.close()}
            className="flex-1 rounded-md bg-navy px-4 py-2.5 text-center text-sm font-semibold text-white transition-colors hover:bg-navy/90"
          >
            Ver planos
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
