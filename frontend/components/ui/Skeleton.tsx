export function Skeleton({ className = "" }: { className?: string }) {
  return (
    <div
      className={`animate-pulse rounded bg-hairline/70 ${className}`}
      aria-hidden
    />
  );
}
