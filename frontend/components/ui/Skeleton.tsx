// `label`: an sr-only status so loading is announced. The bars themselves stay
// aria-hidden — a screen reader reading out six grey rectangles is noise, but
// silence while the page fills is worse.
export function Skeleton({
  className = "",
  label,
}: {
  className?: string;
  label?: string;
}) {
  const bar = (
    // motion-reduce here, not in globals.css: the global reduced-motion block
    // lists the landing page's named animations, and Tailwind's animate-pulse
    // is not one of them — so every loading state on every app page pulsed for
    // a user who asked it not to.
    <div
      className={`animate-pulse rounded bg-hairline/70 motion-reduce:animate-none ${className}`}
      aria-hidden
    />
  );
  if (!label) return bar;
  return (
    <div role="status" aria-busy="true">
      <span className="sr-only">{label}</span>
      {bar}
    </div>
  );
}
