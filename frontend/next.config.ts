import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  poweredByHeader: false,
  distDir: process.env.NEXT_DIST_DIR ?? ".next",
  allowedDevOrigins: ["localhost", "127.0.0.1"],
};

export default nextConfig;
