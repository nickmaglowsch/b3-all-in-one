// The mark IS the Q of the wordmark, so its geometry is fitted to Archivo 800
// rather than chosen by eye. Measured off the live font at 200px:
//   cap height 0.690em · stem 0.180em · Q tail descends 0.135em below baseline
// Ring outer height is 0.710em (cap + 0.010em round-letter overshoot top and
// bottom). Stroke is 0.158em — 88% of the stem, because a monoline ring reads
// heavier than Archivo's bowl, which thins at the top and bottom of the curve.
// viewBox is cropped to the ink so the fit against "u" is sidebearing, not padding.
export function BrandMark({ className = "" }: { className?: string }) {
  return (
    <svg
      viewBox="3.01 2.51 16.98 20.05"
      width="0.71em"
      height="0.838em"
      fill="none"
      stroke="currentColor"
      strokeWidth={3.78}
      strokeLinecap="round"
      aria-hidden="true"
      // Seats the ring on the baseline; the overhang is the tail's descender.
      // marginRight stands in for the right sidebearing the cropped viewBox drops.
      style={{ verticalAlign: "-0.138em", marginRight: "0.045em" }}
      // `inline` overrides Tailwind preflight's `svg { display: block }`, which
      // would otherwise break the line and kill vertical-align.
      className={`inline ${className}`}
    >
      <circle cx="11.5" cy="11" r="6.6" />
      {/* Steep and short, stopping at the bowl's right edge — a 45° tail running
          well clear of the ring reads as a magnifying glass, not a Q. */}
      <path d="M14.5 15 18.6 21" />
    </svg>
  );
}

// Single source for the wordmark. The Q carries the accent; "uantax" stays ink.
// Note the accent reads differently per theme by design: navy vs ink is 1.25:1
// in light mode (near-black, a whisper) and 2.98:1 in dark (an obvious blue Q).
// Swapping --navy for --series-1 here is the one-token change that would make it
// read as an accent in light mode too.
// The sr-only Q keeps the accessible name "Quantax".
export function Brand({ className = "" }: { className?: string }) {
  return (
    <span
      // nowrap: the mark is a glyph, the line must never break before "uantax".
      className={`whitespace-nowrap font-display font-extrabold tracking-tight text-ink ${className}`}
    >
      <span className="sr-only">Q</span>
      <BrandMark className="text-navy" />
      uantax
    </span>
  );
}
