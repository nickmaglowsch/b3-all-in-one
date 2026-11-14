// Is a result on screen still the answer to the inputs on screen? A mutation's
// `data` outlives the form that produced it: change the aporte, the sells or the
// chosen strategies, and the old plan sits under the new inputs reading as their
// answer (or gets acted on). /irpf already ties its report to the year it was
// generated for; this is the same check for a request body — `variables` (what
// the mutation was called with) against what the button would send now.
//
// Pure and import-free, because `npm test` runs this file through `node --test`.

// Plain JSON with object keys sorted, so key order is not a difference; an
// `undefined` field is dropped, the same as JSON.stringify sends it.
const canon = (v: unknown) =>
  JSON.stringify(v, (_k, x: unknown) =>
    x && typeof x === "object" && !Array.isArray(x)
      ? Object.fromEntries(Object.entries(x).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)))
      : x,
  );

/** True when there is a result and it was asked for with other inputs. Pass both
 *  sides through the same key function when an array is a set (sort it). */
export function isStale<T>(sent: T | undefined, now: T, key: (t: T) => unknown = (t) => t) {
  return sent !== undefined && canon(key(sent)) !== canon(key(now));
}

/** A list where order means nothing, as a sorted copy: excluded tickers, picked
 *  strategies. Absent and empty are the same request. */
export const asSet = (xs?: readonly string[]) => [...(xs ?? [])].sort();
