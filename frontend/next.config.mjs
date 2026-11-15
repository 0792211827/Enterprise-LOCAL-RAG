/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  reactStrictMode: true,
  // The browser tests build into their own directories so they never clobber a running dev server.
  distDir: process.env.NEXT_DIST_DIR || ".next",
};
export default nextConfig;
