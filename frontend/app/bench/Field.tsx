// Labelled form control shared by the bench's run panel and strategy builder.

export const inputCls =
  "w-full rounded-md border border-hairline bg-paper px-2.5 py-2 text-sm text-ink focus:border-navy focus:outline-none";

export function Field({
  label,
  hint,
  className,
  children,
}: {
  label: string;
  hint?: string;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <label className={className ? `block ${className}` : "block"}>
      <span className="mb-1 flex items-baseline gap-1.5">
        <span className="text-xs font-medium text-ink">{label}</span>
        {hint && <span className="font-mono text-[11px] text-muted">{hint}</span>}
      </span>
      {children}
    </label>
  );
}
