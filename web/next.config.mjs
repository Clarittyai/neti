/**
 * Static export, on purpose.
 *
 * The console is entirely client-side: every screen fetches the API from the browser and nothing is
 * rendered on a server. So it exports to files, and `neti console` serves those files and the API
 * from one Python process on one port.
 *
 * That is what makes the install one command. The alternative — shipping a Node runtime beside a
 * Python CLI and asking an operator to run two servers — is the kind of friction that decides
 * whether a security tool gets evaluated at all.
 *
 * The constraint it buys: no dynamic route segments, because their values are decisions that have
 * not happened yet. Hence `/decision?id=…` rather than `/decisions/[id]`.
 */
/** @type {import('next').NextConfig} */
export default {
  reactStrictMode: true,
  output: "export",
  // Served from disk, so every route must be a real directory with an index.html rather than a
  // rewrite rule the Python side would otherwise have to reimplement.
  trailingSlash: true,
  images: { unoptimized: true },
};
