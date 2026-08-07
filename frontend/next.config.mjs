/** @type {import('next').NextConfig} */
const isLocalApiBase = (raw) =>
  typeof raw === "string" &&
  /^https?:\/\/(localhost|127\.0\.0\.1)(:\d+)?$/.test(raw);

const nextConfig = {
  // Required for the production Docker image (see deploy/frontend.Dockerfile).
  // `output: "standalone"` produces `.next/standalone/{server.js, .next/, ...}`
  // that is a self-contained Node.js server; runtime does NOT need
  // `next dev` and does NOT need the full `node_modules` tree.
  output: "standalone",
  reactStrictMode: true,
  images: {
    // Production: same-origin, so no remotePatterns needed. The placeholder
    // entries below are added only when the dev-time API base is a local
    // host — they never appear in production bundles.
    remotePatterns: isLocalApiBase(process.env.NEXT_PUBLIC_API_BASE_URL)
      ? [
          { protocol: "http", hostname: "localhost", port: "8000" },
          { protocol: "http", hostname: "127.0.0.1", port: "8000" },
        ]
      : [],
  },
  experimental: {
    typedRoutes: true,
  },
};

export default nextConfig;
