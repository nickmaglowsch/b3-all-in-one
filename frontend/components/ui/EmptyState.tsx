// Direct copy: say what to do next, not "no data".
//
// The dashed border means "there is nothing here" — DESIGN.md makes it the one
// place the border style changes, precisely so emptiness reads at a glance.
// Which is why an outage and a paywall must NOT wear it: an error box spends two
// sentences insisting the data is not gone, inside a frame that says it is, and
// a dashed box around a working feature you can buy is stranger still.
import Link from "next/link";

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

/** The `action` of an offer EmptyState (a 402): where the plan is sold. */
export function InvestorPlanLink() {
  return (
    <Link
      href="/assinatura"
      className="rounded-md bg-navy px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-navy/90"
    >
      Ver o plano Investidor
    </Link>
  );
}
