"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useEffect, useState } from "react";

export function Providers({ children }: { children: React.ReactNode }) {
  // Proof that React came up, read by the recovery script in app/layout.tsx —
  // its "the app never started" timeout has no other way to tell a working page
  // from a prerendered shell whose chunks 404'd after a deploy. Set from the
  // outermost client component, so it means hydration, not merely a route.
  useEffect(() => {
    document.documentElement.setAttribute("data-b3-ready", "");
  }, []);

  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: { staleTime: 30_000, retry: 1, refetchOnWindowFocus: false },
        },
      }),
  );
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
