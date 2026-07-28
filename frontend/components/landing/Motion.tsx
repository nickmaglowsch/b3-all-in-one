"use client";

import { useEffect, useRef, useState } from "react";

// Fade-and-lift a block the first time it scrolls into view. CSS does the
// transition; this only flips the .is-visible class, which also fills the
// stage's node on the signal path and unblocks any chart draw inside it.
// prefers-reduced-motion is handled entirely in globals.css.
// `still` opts a block out of the entrance while keeping the .is-visible state
// its children read (the stage node's fill). The first viewport uses it: content
// already on screen at load has nothing to enter from, and fading it in only
// delays the headline by the transition duration.
export function Reveal({
  children,
  className = "",
  delay = 0,
  still = false,
}: {
  children: React.ReactNode;
  className?: string;
  delay?: number;
  still?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [seen, setSeen] = useState(still);

  useEffect(() => {
    if (still) return;
    const el = ref.current;
    if (!el) return;
    const io = new IntersectionObserver(
      ([e]) => {
        if (e.isIntersecting) {
          setSeen(true);
          io.disconnect();
        }
      },
      { threshold: 0.15, rootMargin: "0px 0px -8% 0px" },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [still]);

  return (
    <div
      ref={ref}
      className={`reveal ${seen ? "is-visible" : ""} ${className}`}
      style={delay ? { transitionDelay: `${delay}ms` } : undefined}
    >
      {children}
    </div>
  );
}
