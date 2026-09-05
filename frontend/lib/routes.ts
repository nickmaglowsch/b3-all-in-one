// Which routes render without the app chrome — and, because AppShell returns the
// page bare for these, without the login guard. That second half is why the list
// lives in its own file with a test: it is an auth decision, not a layout one.

// The privacy policy is legally required to be readable *before* signing up, so
// it belongs here alongside the landing and the login page itself.
export const PUBLIC_ROUTES = ["/", "/login", "/privacidade"];

// Exact match, never a prefix: a prefix rule opens every route someone later adds
// under it. `/variantes/*` was one, and it outlived the landing bake-off it existed for.
export const isPublicRoute = (pathname: string) => PUBLIC_ROUTES.includes(pathname);

// Where a session belongs. The landing, the login screen and the OAuth callback
// all funnel here, so the app has exactly one front door.
export const APP_HOME = "/portfolio";

/** Where the landing's entry actions point: the app itself once there is a
 *  session, the login screen otherwise.
 *
 *  Without this the landing's "Entrar" is a dead end for the one person who
 *  cannot use it — someone already signed in, sent to the sign-in screen. */
export const entryHref = (hasSession: boolean) => (hasSession ? APP_HOME : "/login");

/** Where a login lands, given the `?next=` the guard carried in.
 *
 *  `next` is URL input, so it is whitelisted, never sanitised: same-site
 *  absolute path or nothing. A backslash counts as a slash here — browsers
 *  resolve `/\evil.com` to another origin exactly like `//evil.com`.
 *
 *  A public route is not a destination either. The guard only ever writes a
 *  guarded route into `next`, so a public one is either the loop (`/login`
 *  sending you back to `/login`) or the door the person was trying to get
 *  past (`/`). */
export function postLoginTarget(next: string | null | undefined): string {
  if (!next || !/^\/[^/\\]/.test(next)) return APP_HOME;
  const pathname = next.split(/[?#]/)[0];
  return isPublicRoute(pathname) ? APP_HOME : next;
}
