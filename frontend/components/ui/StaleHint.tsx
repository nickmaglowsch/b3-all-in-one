// Over a result whose inputs changed after it was generated (lib/staleResult.ts).
// The result stays — it is still the last answer, and hiding it on every
// keystroke would make the page jump — but it says which inputs it answers.
export function StaleHint({ action }: { action: string }) {
  return (
    <p
      role="status"
      className="mb-3 rounded-md border border-status-processing/40 bg-status-processing/10 px-3 py-2 text-xs text-ink"
    >
      Os parâmetros mudaram desde este resultado. Clique em “{action}” para gerar de novo.
    </p>
  );
}
