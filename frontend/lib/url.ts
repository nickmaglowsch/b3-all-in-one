// Query strings for API paths. Built by URLSearchParams, never by pasting values
// into a template: a renda fixa holding is named by free text ("TESOURO IPCA+
// 2029", "CDB - CDB24C7HQ2L"), and a raw "+" reaches the server as a space —
// the name then matches nothing and the holding silently drops out.
//
// Pure and import-free, because `npm test` runs this file through `node --test`.

export type QueryValue = string | number | boolean | null | undefined;

/** `path` plus its encoded query; null/undefined params are left out, and no
 *  "?" is added when nothing is left. */
export function withQuery(path: string, params: Record<string, QueryValue>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== null && v !== undefined) q.set(k, String(v));
  }
  const s = q.toString();
  return s ? `${path}?${s}` : path;
}
