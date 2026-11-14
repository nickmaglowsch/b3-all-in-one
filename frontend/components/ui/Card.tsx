export function Card({
  children,
  className = "",
  title,
  action,
  id,
}: {
  children: React.ReactNode;
  className?: string;
  title?: React.ReactNode;
  action?: React.ReactNode;
  /** anchor target */
  id?: string;
}) {
  return (
    <section
      id={id}
      className={`rounded-lg border border-hairline bg-panel ${className}`}
    >
      {/* The header wraps rather than overflowing: card actions grew from a
          single label to filter-plus-page-size, and a header that cannot wrap
          pushes the whole document sideways on a phone. */}
      {(title || action) && (
        <header className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-b border-hairline px-3 py-3 sm:px-4">
          {title && (
            <h2 className="text-sm font-semibold text-ink">{title}</h2>
          )}
          {action}
        </header>
      )}
      <div className="p-3 sm:p-4">{children}</div>
    </section>
  );
}
