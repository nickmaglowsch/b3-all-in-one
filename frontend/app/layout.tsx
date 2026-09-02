import type { Metadata } from "next";
import { Archivo, IBM_Plex_Sans, IBM_Plex_Mono } from "next/font/google";
import "./globals.css";
import { Providers } from "@/lib/providers";
import { AppShell } from "@/components/AppShell";

const archivo = Archivo({
  variable: "--font-archivo",
  subsets: ["latin"],
  weight: ["700", "800"],
});
const plexSans = IBM_Plex_Sans({
  variable: "--font-plex-sans",
  subsets: ["latin"],
  weight: ["400", "500", "600"],
});
const plexMono = IBM_Plex_Mono({
  variable: "--font-plex-mono",
  subsets: ["latin"],
  weight: ["400", "500", "600"],
});

export const metadata: Metadata = {
  title: "Quantax",
  description: "Carteira, IRPF e bench a partir dos seus extratos da B3.",
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
