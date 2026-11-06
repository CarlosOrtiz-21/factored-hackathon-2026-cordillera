import type { NextConfig } from "next";

// Static export: `next build` writes plain HTML/JS/CSS to `out/`. No Node server is needed,
// so the site can be hosted for free (Vercel Hobby, Cloudflare Pages, GitHub Pages...).
// Everything talks to the FastAPI backend from the browser (NEXT_PUBLIC_API_URL).
const config: NextConfig = {
  output: "export",
  trailingSlash: true, // /auditor -> /auditor/index.html, works on any static host
  images: { unoptimized: true },
  reactStrictMode: true,
};

export default config;
