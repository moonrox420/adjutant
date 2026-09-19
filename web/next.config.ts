import type { NextConfig } from "next";

const backend = process.env.ADJUTANT_API_URL || "http://127.0.0.1:8000";
const config: NextConfig = {
  output: "standalone",
  devIndicators: false,
  distDir: process.env.ADJUTANT_NEXT_DIST_DIR || ".next",
  turbopack: { root: process.cwd() },
  experimental: { proxyTimeout: 480_000 },
  poweredByHeader: false,
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${backend}/api/:path*` }];
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
        ],
      },
    ];
  },
};
export default config;
