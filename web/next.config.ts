import type { NextConfig } from "next";

// The Pinned API (FastAPI: /v1/* and the MCP endpoint /mcp). In production a reverse proxy serves both on the site's
// origin; in development Next forwards them to PINNED_API_ORIGIN.
const api = process.env.PINNED_API_ORIGIN ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  cacheComponents: true,
  partialPrefetching: true,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
  async rewrites() {
    return [
      { source: "/v1/:path*", destination: `${api}/v1/:path*` },
      { source: "/mcp", destination: `${api}/mcp` },
    ];
  },
};

export default nextConfig;
