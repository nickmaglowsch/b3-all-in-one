// Which routes render without the app chrome — and, because AppShell returns the
// page bare for these, without the login guard. That second half is why the list
// lives in its own file with a test: it is an auth decision, not a layout one.

// The privacy policy is legally required to be readable *before* signing up, so
// it belongs here alongside the landing and the login page itself.
//
// /auth/callback is here because it is where the token *arrives*, so storage has
// none yet when it renders. Inside the shell, the sidebar's own queries
// (/portfolios, /me) ran before the page's effect could store the token — React
// runs an earlier sibling's effects first — went out without a credential, came
// back 401, and the 401 handler wiped the token Google had just delivered and
// sent the browser to /login. Whichever response won that race decided whether
// the login worked: "sometimes I have to clear cookies to log in". Rendered bare,
// the page runs nothing but its own effect.
export const PUBLIC_ROUTES = ["/", "/login", "/auth/callback", "/privacidade"];

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
  // The URL parser strips tab/LF/CR anywhere, so `/\t/evil.example` passes a
  // check on the raw string and still resolves to `//evil.example`. Refuse
  // control characters and spaces outright, then judge what the browser will
  // resolve, not what was typed.
  if (!next || /[\u0000-\u0020\u007f]/.test(next) || !/^\/[^/\\]/.test(next)) return APP_HOME;
  const base = "https://app.invalid";
  const url = new URL(next, base);
  // Dot segments collapse on parse: `/.//evil.example` comes out as the path
  // `//evil.example`, which the router then resolves to another origin.
  if (url.origin !== base || url.pathname.startsWith("//")) return APP_HOME;
  return isPublicRoute(url.pathname) ? APP_HOME : url.pathname + url.search + url.hash;
}
