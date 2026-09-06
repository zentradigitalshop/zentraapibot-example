import type { NextConfig } from "next";

const config: NextConfig = {
  // Every page reads live data and is behind a session cookie. Nothing here
  // should ever be prerendered at build time or served from a CDN cache.
  experimental: { serverActions: { bodySizeLimit: "1mb" } },
  poweredByHeader: false,
  headers: async () => [
    {
      source: "/:path*",
      headers: [
        { key: "X-Frame-Options", value: "DENY" },
        { key: "X-Content-Type-Options", value: "nosniff" },
        { key: "Referrer-Policy", value: "same-origin" },
        { key: "Cache-Control", value: "no-store, must-revalidate" },
      ],
    },
  ],
};

export default config;
