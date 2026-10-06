/** The browser only talks to this origin; /api/* is proxied to the FORGE API so the session cookie
 *  is first-party (SameSite=Lax) and CORS is not needed. In production a reverse proxy
 *  (Caddy, see infra/Caddyfile) serves both and this rewrite is simply unused. */
const API = process.env.FORGE_API_URL || "http://localhost:8000";

/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  reactStrictMode: true,
  compress: false, // Server-Sent Events must not be buffered by compression
  poweredByHeader: false,
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API}/api/:path*` }];
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Frame-Options", value: "DENY" },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "same-origin" },
        ],
      },
    ];
  },
};
export default nextConfig;
