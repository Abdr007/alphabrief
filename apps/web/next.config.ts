import type { NextConfig } from "next";

/**
 * The browser never talks to the API directly: every call goes through a Next
 * route handler, so the approval token and the API origin stay server-side.
 * That is also why no `NEXT_PUBLIC_*` API variable exists.
 */
const nextConfig: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  async headers() {
    return [
      {
        source: "/(.*)",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          {
            key: "Permissions-Policy",
            value: "geolocation=(), microphone=(), camera=(), payment=()",
          },
        ],
      },
    ];
  },
};

export default nextConfig;
