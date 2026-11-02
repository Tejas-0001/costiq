import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // The Docker image runs the minimal standalone server; local `next start` keeps the default output.
  output: process.env.COSTIQ_STANDALONE === "1" ? "standalone" : undefined,
  experimental: {
    agentFeedback: true,
  },
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
};

export default nextConfig;
