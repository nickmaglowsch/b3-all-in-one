export function Card({
  children,
  className = "",
  title,
  action,
}: {
  children: React.ReactNode;
  className?: string;
  title?: React.ReactNode;
  action?: React.ReactNode;
}) {
  return (
    <section
      className={`rounded-lg border border-hairline bg-panel ${className}`}
    >
      {(title || action) && (
        <header className="flex items-center justify-between gap-4 border-b border-hairline px-4 py-3">
          {title && (
            <h2 className="text-sm font-semibold text-ink">{title}</h2>
          )}
          {action}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}
