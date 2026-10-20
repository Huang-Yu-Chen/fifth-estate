import { mkdirSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";
import { PAGES, SITE_DESCRIPTION, SITE_NAME, SITE_URL, outletDescription, pageTitle, unitDescription } from "./src/site.ts";

// Served from the root of https://the-fifth-estate.org by Firebase Hosting (firebase.json), with real paths such as
// /unit/topic/defense. Each fixed page gets its own copy of index.html (/events → events.html) with its title,
// description, canonical URL and link-preview tags. The pages that depend on the data (units, including events
// that appear after this build, and outlets), sitemap.xml and robots.txt are rendered by the API from its current
// snapshot (src/fifth_estate/api/pages.py; Firebase sends those paths there), out of page-templates.json written
// here: they load this build's scripts and use the same wording (src/site.ts). Any other path gets 404.html.
// The data comes from the API (VITE_API_BASE in .env.production); `npm run dev` proxies /v1 to the local API.
export default defineConfig({
  plugins: [react(), routePages()],
  build: { chunkSizeWarningLimit: 1200 },
  server: { proxy: { "/v1": "http://127.0.0.1:8000" } },
});

interface Page {
  path: string;
  name?: string; // none for the overview
  description: string;
}

function routePages(): Plugin {
  let root = "";
  let dist = "";
  return {
    name: "route-pages",
    apply: "build",
    configResolved(config) {
      root = config.root;
      dist = resolve(config.root, config.build.outDir);
    },
    closeBundle() {
      // Kept out of search engines while the site is in beta; the API decides the same for its pages from the snapshot.
      const indexable = siteStatus(resolve(root, "../config/pipeline.yaml")) !== "beta";
      const fixed: Page[] = [
        { path: "/", description: SITE_DESCRIPTION },
        ...Object.entries(PAGES).map(([p, { title, description }]) => ({ path: `/${p}`, name: title, description })),
      ];
      const template = readFileSync(resolve(dist, "index.html"), "utf8");
      const write = (file: string, text: string) => {
        mkdirSync(dirname(resolve(dist, file)), { recursive: true });
        writeFileSync(resolve(dist, file), text);
      };
      for (const p of fixed) write(p.path === "/" ? "index.html" : `${p.path.slice(1)}.html`, html(template, p, indexable));
      const notFound = html(template, { path: "", description: SITE_DESCRIPTION }, false);
      write("404.html", notFound);
      // For the API: the page with slots for path, name and description, and how descriptions are worded.
      const slots = { path: "@@PATH@@", name: "@@NAME@@", description: "@@DESCRIPTION@@" };
      const templates = {
        site_url: SITE_URL,
        pages: fixed.map((p) => p.path), // listed in the sitemap with the units and outlets
        page: { index: html(template, slots, true), noindex: html(template, slots, false) },
        not_found: notFound,
        describe: {
          event: unitDescription("event", "{name}"),
          unit: unitDescription("topic", "{name}", "{detail}"),
          unit_plain: unitDescription("topic", "{name}"),
          outlet: outletDescription("{name}"),
        },
        // Kept through the next deploy (site.yml), for pages loaded or rendered before it
        assets: readdirSync(resolve(dist, "assets")).map((f) => `assets/${f}`),
      };
      write("page-templates.json", JSON.stringify(templates));
    },
  };
}

/** site.status from config/pipeline.yaml (a plain `status: value` line in the `site:` block); beta if not found. */
function siteStatus(file: string): string {
  let text = "";
  try {
    text = readFileSync(file, "utf8");
  } catch {
    console.warn(`route pages: no ${file}; the pages get noindex`);
  }
  const block = /^site:[^\n]*\n((?:[ \t]+[^\n]*\n|[ \t]*\n)*)/m.exec(text)?.[1] ?? "";
  return /^[ \t]+status:[ \t]*["']?([\w-]+)/m.exec(block)?.[1] ?? "beta";
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
