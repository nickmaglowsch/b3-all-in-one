"use client";

import { useEffect, useState } from "react";

/**
 * Sticky in-page nav for the long stacked-card pages. The anchors do the
 * scrolling (native); an IntersectionObserver only marks where you are.
 * Anchor targets need `scroll-mt-28 md:scroll-mt-16` to clear the sticky bars.
 */
export function SectionNav({ items }: { items: { id: string; label: string }[] }) {
  const [active, setActive] = useState("");
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

  return (
    <nav
      aria-label="Seções da página"
      className="sticky top-14 z-10 mb-4 overflow-x-auto border-b border-hairline bg-paper md:top-0"
    >
      <ul className="flex gap-1 py-2">
        {items.map(({ id, label }) => (
          <li key={id}>
            <a
              href={`#${id}`}
              aria-current={active === id ? "true" : undefined}
              className={`block whitespace-nowrap rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${
                active === id
                  ? "bg-navy/[0.06] text-navy"
                  : "text-muted hover:bg-hairline/40 hover:text-ink"
              }`}
            >
              {label}
            </a>
          </li>
        ))}
      </ul>
    </nav>
  );
}
