// `label`: an sr-only status so loading is announced. The bars themselves stay
// aria-hidden — a screen reader reading out six grey rectangles is noise, but
// silence while the page fills is worse.
//
// The live region is NOT this component's job to own. Unmounting a role="status"
// node destroys the region instead of updating it, so nothing ever announces
// that loading finished, and a permanently-true aria-busy suppresses live output
// in some AT for as long as it is set. `LoadingStatus` below stays mounted across
// both states and swaps its text, which is what actually gets announced.
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
    <>
      <LoadingStatus busy label={label} />
      {bar}
    </>
  );
}

/**
 * A persistent polite live region. Render it on both sides of a loading branch —
 * same position in the tree — so the region survives the transition and the
 * change of text is what gets announced. Mount it only while busy and AT hears
 * nothing at all, because a region that appears already-populated is not a
 * change.
 */
export function LoadingStatus({
  busy,
  label,
  done = "",
}: {
  busy: boolean;
  label: string;
  done?: string;
}) {
  return (
    <span role="status" aria-live="polite" className="sr-only">
      {busy ? label : done}
    </span>
  );
}
