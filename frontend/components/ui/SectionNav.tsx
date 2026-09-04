"use client";

import { useEffect, useRef, useState } from "react";

/**
 * Sticky in-page nav for the long stacked-card pages. The anchors do the
 * scrolling (native); an IntersectionObserver only marks where you are.
 * Anchor targets need `scroll-mt-28 md:scroll-mt-16` to clear the sticky bars.
 */
export function SectionNav({
  items,
  trailing,
}: {
  items: { id: string; label: string }[];
  /** Right-aligned, stays on screen with the bar. Used for the active client
   *  book's name, which otherwise lives only in the header and is gone after
   *  200px of scroll — on a page whose whole job is showing someone's money. */
  trailing?: React.ReactNode;
}) {
  const [active, setActive] = useState("");
  const strip = useRef<HTMLUListElement>(null);
  const key = items.map((i) => i.id).join(",");

  useEffect(() => {
    const ids = key.split(",");
    const els = ids
      .map((id) => document.getElementById(id))
      .filter((el): el is HTMLElement => !!el);
    if (!els.length) return;

    const visible = new Set<string>();
    const io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          if (e.isIntersecting) visible.add(e.target.id);
          else visible.delete(e.target.id);
        }
        const first = ids.find((id) => visible.has(id));
        if (first) setActive(first);
      },
      { rootMargin: "-120px 0px -55% 0px" },
    );
    els.forEach((el) => io.observe(el));
    return () => io.disconnect();
  }, [key]);

  // Seven sections do not fit across 375px, so the strip scrolls — and a strip
  // that scrolls while the page does is a strip whose current chip drifts out of
  // sight. Follow the observer's answer, no smoother than the section change
  // itself. `block: "nearest"` so this never scrolls the *page*, only the strip.
  useEffect(() => {
    if (!active) return;
    strip.current
      ?.querySelector(`[data-chip="${active}"]`)
      ?.scrollIntoView({ inline: "nearest", block: "nearest", behavior: "smooth" });
  }, [active]);

  return (
    // Full-bleed on a phone: the strip's scroll runs to the screen's edges, the
    // way a native segmented control does, instead of stopping short inside the
    // page gutter and reading as a clipped box. `-mx-4 px-4` cancels `main`'s
    // padding and gives it back inside the scroller, so the first and last chips
    // still have their gutter. The scrollbar itself is chrome, not information.
    <nav
      aria-label="Seções da página"
      className="sticky top-[calc(3.5rem+env(safe-area-inset-top,0px))] z-20 -mx-4 mb-4 overflow-x-auto overscroll-x-contain border-b border-hairline bg-paper/90 px-4 backdrop-blur-md no-scrollbar md:top-0 md:mx-0 md:px-0 md:backdrop-blur-none"
    >
      <ul ref={strip} className="flex items-center gap-1 py-2">
        {items.map(({ id, label }) => (
          <li key={id}>
            <a
              href={`#${id}`}
              data-chip={id}
              aria-current={active === id ? "true" : undefined}
              className={`block whitespace-nowrap rounded-full px-3 py-2 text-xs font-medium transition-colors md:rounded-md md:py-1.5 ${
                active === id
                  ? "bg-navy/[0.06] text-navy"
                  : "text-muted active:bg-hairline/60 hover:bg-hairline/40 hover:text-ink"
              }`}
            >
              {label}
            </a>
          </li>
        ))}
        {trailing && (
          <li className="ml-auto shrink-0 pl-3 pr-1">{trailing}</li>
        )}
      </ul>
    </nav>
  );
}
