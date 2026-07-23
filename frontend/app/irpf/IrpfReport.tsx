"use client";

import { useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { IconCheck } from "@/components/icons";
import { mdComponents } from "./mdComponents";

interface Section {
  id: string;
  title: string; // full heading, e.g. "1) Bens e Direitos — Ficha..."
  nav: string; // short label for the rail
  raw: string; // full section markdown incl. the "## " line (this is what copies)
  body: string; // section minus the heading line (rendered)
}

// Split the report on H2 (## ) boundaries. Everything before the first H2 (the
// H1 title + intro/disclaimer) is the preamble.
function splitReport(md: string): { preamble: string; sections: Section[] } {
  const lines = md.split("\n");
  const preamble: string[] = [];
  const sections: Section[] = [];
  let cur: string[] | null = null;

  for (const line of lines) {
    if (/^##\s+/.test(line)) {
      cur = [line];
      const title = line.replace(/^##\s+/, "").trim();
      sections.push({
        id: `sec-${sections.length}`,
        title,
        nav: title.replace(/^\d+\)\s*/, "").split(" — ")[0],
        raw: "", // filled below
        body: "",
      });
    } else if (cur) {
      cur.push(line);
    } else {
      preamble.push(line);
    }
    if (cur) sections[sections.length - 1].raw = cur.join("\n");
  }
  for (const s of sections) {
    const [, ...rest] = s.raw.split("\n");
    s.body = rest.join("\n");
  }
  return { preamble: preamble.join("\n").trim(), sections };
}

export function IrpfReport({ markdown }: { markdown: string }) {
  const { preamble, sections } = useMemo(
    () => splitReport(markdown),
    [markdown],
  );

  return (
    <div className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_190px]">
      {/* min-w-0 lets wide tables scroll inside the cell instead of blowing out the grid */}
      <div className="min-w-0">
        {preamble && (
          <div className="mb-8">
            <ReactMarkdown remarkPlugins={[remarkGfm]} components={mdComponents}>
              {preamble}
            </ReactMarkdown>
          </div>
        )}
        <div className="space-y-10">
          {sections.map((s) => (
            <SectionBlock key={s.id} section={s} />
          ))}
        </div>
      </div>

      <nav className="hidden lg:block">
        <div className="sticky top-8">
          <p className="mb-2 font-mono text-[11px] font-medium uppercase tracking-[0.14em] text-muted">
            Seções
          </p>
          <ol className="space-y-0.5">
            {sections.map((s, i) => (
              <li key={s.id}>
                <a
                  href={`#${s.id}`}
                  className="flex items-baseline gap-2 rounded-md px-2 py-1.5 text-sm text-muted transition-colors hover:bg-hairline/40 hover:text-ink"
                >
                  <span className="font-mono text-[11px] text-muted">
                    {i + 1}
                  </span>
                  <span className="leading-snug">{s.nav}</span>
                </a>
              </li>
            ))}
          </ol>
        </div>
      </nav>
    </div>
  );
}

function SectionBlock({ section }: { section: Section }) {
  return (
    <section id={section.id} className="scroll-mt-8">
      <div className="mb-4 flex items-start justify-between gap-4 border-b border-hairline pb-2">
        <h2 className="font-display text-lg font-bold leading-tight text-ink">
          {section.title}
        </h2>
        <CopyButton text={section.raw} />
      </div>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={mdComponents}>
        {section.body}
      </ReactMarkdown>
    </section>
  );
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    } catch {
      /* clipboard blocked — no-op; ponytail: localhost is a secure context */
    }
  }
  return (
    <button
      type="button"
      onClick={copy}
      className={`inline-flex shrink-0 items-center gap-1.5 rounded-md border px-2.5 py-1 text-xs font-medium transition-colors ${
        copied
          ? "border-status-done/30 bg-status-done/10 text-status-done"
          : "border-hairline text-muted hover:bg-hairline/40 hover:text-ink"
      }`}
      title="Copiar o markdown desta seção"
    >
      {copied ? <IconCheck size={13} /> : null}
      {copied ? "Copiado" : "Copiar seção"}
    </button>
  );
}
