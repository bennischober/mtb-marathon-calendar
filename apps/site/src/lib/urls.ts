/**
 * Site-absolute paths must carry the configured base path when the site is
 * served from a subdirectory (GitHub Pages project sites serve under
 * /<repo>/). Astro's `base` option prefixes generated URLs but does NOT
 * rewrite hardcoded hrefs, so every internal link goes through here.
 *
 * Works in .astro frontmatter and in client scripts alike: Vite statically
 * replaces import.meta.env at build time.
 */
const BASE = import.meta.env.BASE_URL.replace(/\/$/, "");

/** Prefix a site-absolute path ("/calendar") with the configured base path. */
export function withBase(path: string): string {
  if (/^https?:\/\//.test(path) || path.startsWith("//")) {
    return path;
  }
  return `${BASE}${path}`;
}
