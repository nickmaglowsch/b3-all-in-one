// Which routes render without the app chrome — and, because AppShell returns the
// page bare for these, without the login guard. That second half is why the list
// lives in its own file with a test: it is an auth decision, not a layout one.

// The privacy policy is legally required to be readable *before* signing up, so
// it belongs here alongside the landing and the login page itself.
export const PUBLIC_ROUTES = ["/", "/login", "/privacidade"];

// Exact match, never a prefix: a prefix rule opens every route someone later adds
// under it. `/variantes/*` was one, and it outlived the landing bake-off it existed for.
export const isPublicRoute = (pathname: string) => PUBLIC_ROUTES.includes(pathname);
