export function PageHeader({
  title,
  eyebrow,
  children,
}: {
  title: string;
  eyebrow?: string;
  children?: React.ReactNode; // right-aligned actions
}) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-x-4 gap-y-2 md:mb-6">
      <div className="min-w-0">
        {eyebrow && (
          <p className="mb-1 font-mono text-[11px] font-medium uppercase tracking-[0.14em] text-muted">
            {eyebrow}
          </p>
        )}
        <h1 className="font-display text-xl font-extrabold leading-tight tracking-tight text-ink sm:text-2xl">
          {title}
        </h1>
      </div>
      {children && (
        <div className="flex flex-wrap items-center gap-2">{children}</div>
      )}
    </div>
  );
}
