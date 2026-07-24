import type { NextConfig } from "next";

// Static export → Cloudflare Pages. The app is entirely client-side (Bearer token
// in localStorage, every fetch goes to NEXT_PUBLIC_API_URL), so there is nothing
// to run on a server. No dynamic route segments — see app/bench/run/page.tsx.
const nextConfig: NextConfig = {
  output: "export",
};

export default nextConfig;
