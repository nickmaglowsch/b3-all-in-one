// Direct copy: say what to do next, not "no data".
//
// The dashed border means "there is nothing here" — DESIGN.md makes it the one
// place the border style changes, precisely so emptiness reads at a glance.
// Which is why an outage and a paywall must NOT wear it: an error box spends two
// sentences insisting the data is not gone, inside a frame that says it is, and
// a dashed box around a working feature you can buy is stranger still.
import Link from "next/link";
import { errorText, requiredModuleOf } from "@/lib/api";
import { ligarHref, moduloDe, nomeDe } from "@/lib/modules";

type Variant = "empty" | "error" | "offer";

const FRAME: Record<Variant, string> = {
  empty: "border-dashed border-hairline",
  error: "border-solid border-status-error/40",
  offer: "border-solid border-hairline",
};

const ICON: Record<Variant, string> = {
  empty: "text-muted",
  error: "text-status-error",
  offer: "text-navy",
};

export function EmptyState({
  title,
  hint,
  action,
  icon,
  variant = "empty",
}: {
  title: string;
  hint?: string;
  action?: React.ReactNode;
  icon?: React.ReactNode;
  variant?: Variant;
}) {
  return (
    <div
      className={`flex flex-col items-center justify-center rounded-lg border bg-panel px-6 py-16 text-center ${FRAME[variant]}`}
    >
      {icon && <div className={`mb-3 ${ICON[variant]}`}>{icon}</div>}
      <p className="max-w-sm text-sm font-medium text-ink">{title}</p>
      {hint && <p className="mt-1 max-w-sm text-sm text-muted">{hint}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

/** The `action` of an error EmptyState: ask the failed request again. */
export function RetryButton({ onClick }: { onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="rounded-md border border-hairline px-3 py-1.5 text-sm font-medium text-ink transition-colors hover:bg-hairline/40"
    >
      Tentar de novo
    </button>
  );
}

/** The `action` of an offer EmptyState (a 402): the subscription page with the
 *  module the gate asked for already in the cart, named on the button. There is
 *  no "plano" to send anyone to — the catalogue is one module per route group. */
export function ModuleOfferLink({ id }: { id: string }) {
  return (
    <Link
      href={ligarHref(id)}
      className="rounded-md bg-navy px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-navy/90"
    >
      Ligar {nomeDe(id)}
    </Link>
  );
}

/**
 * What a section shows when its on-load query failed: the inline offer for a
 * 402, otherwise an error with a retry. The pair every gated screen needs —
 * those queries pass `paywallDialog: false`, so this is the only place the
 * user learns which module the panel belongs to.
 */
export function LoadFailure({
  error,
  title,
  offerTitle = (nome) => `Isto vem com o módulo ${nome}.`,
  offerHint,
  icon,
  onRetry,
}: {
  error: unknown;
  /** The error sentence: what could not be loaded. */
  title: string;
  /** The offer sentence, given the module's name — the gate decides which. */
  offerTitle?: (moduleName: string) => string;
  /** What the panel does, when the screen can say it better than the catalogue. */
  offerHint?: string;
  icon?: React.ReactNode;
  onRetry?: () => void;
}) {
  const gate = requiredModuleOf(error);
  if (gate) {
    return (
      <EmptyState
        variant="offer"
        icon={icon}
        title={offerTitle(nomeDe(gate))}
        hint={offerHint ?? moduloDe(gate)?.carga}
        action={<ModuleOfferLink id={gate} />}
      />
    );
  }
  return (
    <EmptyState
      variant="error"
      icon={icon}
      title={title}
      hint={errorText(error, "A conexão com a API falhou. Tente de novo em instantes.")}
      action={onRetry ? <RetryButton onClick={onRetry} /> : undefined}
    />
  );
}
