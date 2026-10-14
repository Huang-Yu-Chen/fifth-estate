import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";
import { PAGES, SITE_DESCRIPTION, SITE_NAME, SITE_URL, outletDescription, pageTitle, unitDescription } from "./src/site.ts";
import type { IndexDoc, Meta } from "./src/types.ts";

// Served from the root of https://the-fifth-estate.org (GitHub Pages with a custom domain), with real paths such
// as /unit/topic/defense. GitHub Pages has no rewrite rules, so after the build every route gets its own copy of
// index.html (/events → events.html) with that page's title, description and canonical URL: each page loads with
// status 200 and gets a proper link preview. Any other path is served 404.html, which runs the app as well.
export default defineConfig({
  plugins: [react(), routePages()],
  build: { chunkSizeWarningLimit: 1200 },
});

interface Page {
  path: string;
  name?: string; // none for the overview
  description: string;
}

function routePages(): Plugin {
  let dist = "";
  return {
    name: "route-pages",
    apply: "build",
    configResolved(config) {
      dist = resolve(config.root, config.build.outDir);
    },
    closeBundle() {
      const json = <T,>(file: string): T | undefined => {
        try {
          return JSON.parse(readFileSync(resolve(dist, "data", file), "utf8")) as T;
        } catch {
          return undefined; // built without dashboard data (web/public/data is written by `fifth publish`)
        }
      };
      const meta = json<Meta>("meta.json");
      const index = json<IndexDoc>("index.json") ?? {};
      // Kept out of search engines while the site is in beta (config/pipeline.yaml site.status), and test data always.
      const indexable = !!meta && !meta.fake_data && meta.site?.status !== "beta";
      const detail = new Map<string, string>([
        ...(meta?.topics ?? []).map((t) => [`topic/${t.id}`, t.description] as const),
        ...(meta?.subtopics ?? []).map((s) => [`subtopic/${s.id}`, s.desc] as const),
      ]);
      const pages: Page[] = [
        { path: "/", description: SITE_DESCRIPTION },
        ...Object.entries(PAGES).map(([p, { title, description }]) => ({ path: `/${p}`, name: title, description })),
        ...Object.entries(index).flatMap(([kind, units]) =>
          (units ?? []).map((u) => ({
            path: `/unit/${kind}/${u.id}`,
            name: u.name,
            description: unitDescription(kind, u.name, detail.get(`${kind}/${u.id}`)),
          })),
        ),
        ...(meta?.outlets ?? []).map((o) => ({ path: `/outlet/${o.id}`, name: o.name, description: outletDescription(o.name) })),
      ].filter((p) => /^\/[\w/-]*$/.test(p.path)); // ids are slugs; anything else is left to 404.html

      const template = readFileSync(resolve(dist, "index.html"), "utf8");
      const write = (file: string, text: string) => {
        mkdirSync(dirname(resolve(dist, file)), { recursive: true });
        writeFileSync(resolve(dist, file), text);
      };
      for (const p of pages) write(p.path === "/" ? "index.html" : `${p.path.slice(1)}.html`, html(template, p, indexable));
      write("404.html", html(template, { path: "", description: SITE_DESCRIPTION }, false));
      write("robots.txt", `User-agent: *\nAllow: /\n${indexable ? `\nSitemap: ${SITE_URL}/sitemap.xml\n` : ""}`);
      if (indexable) {
        const urls = pages.map((p) => `  <url><loc>${SITE_URL}${p.path}</loc></url>`).join("\n");
        write("sitemap.xml", `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n${urls}\n</urlset>\n`);
      }
    },
  };
}

function html(template: string, page: Page, indexable: boolean): string {
  const url = page.path && SITE_URL + page.path;
  const head = [
    !indexable && `<meta name="robots" content="noindex" />`,
    url && `<link rel="canonical" href="${url}" />`,
    `<meta property="og:type" content="website" />`,
    `<meta property="og:site_name" content="${SITE_NAME}" />`,
    `<meta property="og:locale" content="zh_TW" />`,
    `<meta property="og:title" content="${escape(page.name ?? SITE_NAME)}" />`,
    `<meta property="og:description" content="${escape(page.description)}" />`,
    url && `<meta property="og:url" content="${url}" />`,
    `<meta name="twitter:card" content="summary" />`,
  ].filter(Boolean);
  return template
    .replace(/<title>[^<]*<\/title>/, () => `<title>${escape(pageTitle(page.name))}</title>`)
    .replace(/<meta name="description" content="[^"]*"/, () => `<meta name="description" content="${escape(page.description)}"`)
    .replace("</head>", () => `  ${head.join("\n    ")}\n  </head>`);
}

function escape(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
