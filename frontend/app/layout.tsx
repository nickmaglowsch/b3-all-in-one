import type { Metadata, Viewport } from "next";
import localFont from "next/font/local";
import "./globals.css";
import { Providers } from "@/lib/providers";
import { AppShell } from "@/components/AppShell";

// Self-hosted (latin subset, woff2, SIL OFL — see app/fonts/OFL-*.txt) so the build
// never has to reach Google. Archivo and IBM Plex Sans are one variable
// file each; IBM Plex Mono is static, one file per weight.
const archivo = localFont({
  variable: "--font-archivo",
  display: "swap",
  src: [{ path: "./fonts/Archivo-latin-variable.woff2", weight: "700 800" }],
});
const plexSans = localFont({
  variable: "--font-plex-sans",
  display: "swap",
  src: [{ path: "./fonts/IBMPlexSans-latin-variable.woff2", weight: "400 600" }],
});
const plexMono = localFont({
  variable: "--font-plex-mono",
  display: "swap",
  src: [
    { path: "./fonts/IBMPlexMono-400-latin.woff2", weight: "400" },
    { path: "./fonts/IBMPlexMono-500-latin.woff2", weight: "500" },
    { path: "./fonts/IBMPlexMono-600-latin.woff2", weight: "600" },
  ],
});

export const metadata: Metadata = {
  title: "Quantax",
  description: "Carteira, IRPF e bench a partir dos seus extratos da B3.",
  // Added to the home screen, this is an app: its own title, its own status bar,
  // no Safari chrome around it.
  applicationName: "Quantax",
  appleWebApp: { capable: true, title: "Quantax", statusBarStyle: "default" },
  formatDetection: { telephone: false },
};

// `viewportFit: "cover"` is what lets the page paint under the notch and the home
// indicator; `.pt-safe`/`.pb-safe` in globals.css keep the content out from under
// them. Without it iOS letterboxes the app in the safe area and the top bar stops
// meeting the screen's edge — the single clearest "this is a web page" tell.
//
// Deliberately no `maximumScale`/`userScalable: false`: pinch-zoom is an
// accessibility affordance, and the reason people reach for that flag — iOS
// zooming into a 12px input and never zooming out — is fixed at the source in
// globals.css, by rendering the fields at 16px on phones.
export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#fafaf8" },
    { media: "(prefers-color-scheme: dark)", color: "#1f1d1a" },
  ],
};

// Last resort for a document that outlived the build it names. public/_headers
// stops the browser reusing HTML without revalidating, which is the fix; this is
// for the gap that remains — a tab left open across a deploy, an intermediary
// that ignores must-revalidate, a browser still holding a document cached before
// those headers existed. In all three the page asks for /_next/static/… chunks
// that the current deployment does not have, gets the 404 page where JavaScript
// should be, and never hydrates. Nothing inside React can answer that, because
// React is what failed to start — so this runs before it, from the document
// head, in plain ES5.
//
// Two triggers, because one is not enough on its own: the error listener sees
// chunks that fail after it is installed, and the timeout covers the ones that
// failed before. Recovery is capped at one attempt per tab, so a page broken for
// any *other* reason reloads once and then stays put instead of looping.
//
// Reloading is not enough by itself either: location.reload() re-serves the
// stale document. Re-fetching with cache: "reload" replaces it in the HTTP cache
// first, so the reload that follows reads the new build.
const RECOVER_STALE_BUILD = `
(function () {
  var KEY = "b3:recovered-stale-build";
  function recover() {
    try {
      if (sessionStorage.getItem(KEY)) return;
      sessionStorage.setItem(KEY, "1");
    } catch (e) { return; }
    var go = function () { location.reload(); };
    try {
      fetch(location.href, { cache: "reload", credentials: "same-origin" }).then(go, go);
    } catch (e) { go(); }
  }
  addEventListener("error", function (e) {
    var el = e.target;
    if (!el || !el.tagName) return;
    if (String(el.src || el.href || "").indexOf("/_next/static/") > -1) recover();
  }, true);
  addEventListener("load", function () {
    setTimeout(function () {
      if (!document.documentElement.hasAttribute("data-b3-ready")) recover();
    }, 5000);
  });
})();
`;

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html
      lang="pt-BR"
      className={`${archivo.variable} ${plexSans.variable} ${plexMono.variable} h-full`}
    >
      <head>
        <script dangerouslySetInnerHTML={{ __html: RECOVER_STALE_BUILD }} />
      </head>
      <body className="min-h-full">
        <Providers>
          <AppShell>{children}</AppShell>
        </Providers>
      </body>
    </html>
  );
}
