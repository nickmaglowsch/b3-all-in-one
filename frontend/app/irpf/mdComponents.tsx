// react-markdown component overrides: render the IRPF report's GFM with the
// app's design tokens (hairlines, mono tabular numerics) — the DataTable
// aesthetic, without touching the frozen shared component.
import type { ComponentProps, ReactNode } from "react";
import { Children, isValidElement } from "react";
import type { Components } from "react-markdown";

// Flatten a cell's children to plain text so numeric columns get mono/right.
function textOf(node: ReactNode): string {
  if (node == null || node === false) return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(textOf).join("");
  if (isValidElement(node))
    return textOf((node.props as { children?: ReactNode }).children);
  return "";
}

// Money / qty / percent cells: digits only + currency/sign punctuation.
const isNumeric = (s: string) =>
  /\d/.test(s) && /^[\s\d.,+\-r$%()]*$/i.test(s.trim());

export const mdComponents: Components = {
  h1: ({ children }) => (
    <h1 className="font-display text-xl font-extrabold tracking-tight text-ink">
      {children}
    </h1>
  ),
  h2: ({ children }) => (
    <h2 className="mt-6 font-display text-lg font-bold text-ink">{children}</h2>
  ),
  h3: ({ children }) => (
    <h3 className="mt-6 mb-2 text-sm font-semibold text-ink">{children}</h3>
  ),
  p: ({ children }) => (
    <p className="my-3 text-sm leading-relaxed text-muted">{children}</p>
  ),
  strong: ({ children }) => (
    <strong className="font-semibold text-ink">{children}</strong>
  ),
  em: ({ children }) => <em className="italic">{children}</em>,
  a: ({ children, href }) => (
    <a href={href} className="text-navy underline underline-offset-2">
      {children}
    </a>
  ),
  code: ({ children }) => (
    <code className="rounded border border-hairline bg-paper px-1 py-0.5 font-mono text-[12px] text-ink">
      {children}
    </code>
  ),
  hr: () => <hr className="my-6 border-hairline" />,
  blockquote: ({ children }) => (
    <blockquote className="my-4 rounded-md border border-hairline border-l-2 border-l-status-processing bg-paper px-4 py-2 text-sm text-muted [&_p]:my-1 [&_p]:text-muted">
      {children}
    </blockquote>
  ),
  ul: ({ children, className }) => {
    const task = className?.includes("contains-task-list");
    return (
      <ul
        className={`my-3 space-y-1.5 text-sm text-muted ${
          task ? "list-none pl-0" : "list-disc pl-5"
        }`}
      >
        {children}
      </ul>
    );
  },
  ol: ({ children }) => (
    <ol className="my-3 list-decimal space-y-1.5 pl-5 text-sm text-muted">
      {children}
    </ol>
  ),
  li: ({ children, className }) => (
    <li className={className?.includes("task-list-item") ? "flex gap-2" : ""}>
      {children}
    </li>
  ),
  input: ({ checked }: ComponentProps<"input">) => (
    <input
      type="checkbox"
      checked={!!checked}
      readOnly
      className="mt-0.5 h-3.5 w-3.5 shrink-0 accent-navy"
    />
  ),
  table: ({ children }) => (
    <div className="my-4 overflow-x-auto rounded-lg border border-hairline">
      <table className="w-full border-collapse text-sm">{children}</table>
    </div>
  ),
  thead: ({ children }) => <thead>{children}</thead>,
  tr: ({ children }) => (
    <tr className="border-b border-hairline/70 last:border-0">{children}</tr>
  ),
  th: ({ children }) => (
    <th className="whitespace-nowrap bg-panel px-3 py-2 text-left text-[11px] font-semibold uppercase tracking-wide text-muted">
      {children}
    </th>
  ),
  td: ({ children }) => {
    const num = isNumeric(textOf(Children.toArray(children)));
    return (
      <td
        className={`px-3 py-2 align-top text-ink ${
          num
            ? "whitespace-nowrap text-right font-mono tnum tabular-nums"
            : "text-left"
        }`}
      >
        {children}
      </td>
    );
  },
};
