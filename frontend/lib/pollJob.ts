// Wait for a queued job: start it, then re-read it until it is no longer busy.
// The what-if replay (POST /whatif → GET /whatif/{id}) is the caller; the loop
// is here, pure and with its I/O injected, because `npm test` runs lib/ through
// `node --test` and a poller is exactly the code worth pinning there.
import { isBusy } from "./jobStatus.ts";

export interface Job {
  id: string;
  status: string;
}

export interface PollOptions {
  everyMs: number;
  /** Give up (reject with `timedOut()`) once this much time has passed. */
  giveUpMs: number;
  timedOut: () => Error;
  sleep?: (ms: number) => Promise<void>;
  now?: () => number;
}

const realSleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

export async function pollJob<J extends Job>(
  start: () => Promise<J>,
  read: (id: string) => Promise<J>,
  { everyMs, giveUpMs, timedOut, sleep = realSleep, now = Date.now }: PollOptions,
): Promise<J> {
  let job = await start();
  const deadline = now() + giveUpMs;
  while (isBusy(job.status)) {
    if (now() > deadline) throw timedOut();
    await sleep(everyMs);
    job = await read(job.id);
  }
  return job;
}
