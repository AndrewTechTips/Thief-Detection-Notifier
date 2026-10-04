/// <reference types="node" />

import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";

import tailwindcss from "@tailwindcss/vite";
import { defineConfig, loadEnv } from "vite";

/** Files from public/ the installed app needs offline (the rest load when used). */
const PUBLIC_PRECACHE = ["/favicon.svg", "/manifest.webmanifest", "/icons/icon-192.png"];

/**
 * Builds sw/service-worker.js into dist/sw.js, filling in this build's files to precache and a
 * version derived from them (new files, new version, new cache). Build only: the dev server
 * never registers a service worker.
 * @returns {import("vite").Plugin}
 */
function serviceWorker() {
  return {
    name: "vision-hub:service-worker",
    apply: "build",
    async generateBundle(_options, bundle) {
      const built = Object.keys(bundle).filter(
        (name) =>
          /\.(js|css)$/.test(name) ||
          // One font file covers Latin text; other scripts load their subsets on demand.
          /onest-latin-wght-normal-[\w-]+\.woff2$/.test(name),
      );
      const precache = [...built.map((name) => `/${name}`), ...PUBLIC_PRECACHE];
      const version = createHash("sha256").update(precache.join("\n")).digest("hex").slice(0, 12);
      const template = await readFile(new URL("./sw/service-worker.js", import.meta.url), "utf8");
      const source = template
        .replace('const VERSION = "__VERSION__";', `const VERSION = ${JSON.stringify(version)};`)
        .replace(
          "const PRECACHE = __PRECACHE__;",
          `const PRECACHE = ${JSON.stringify(precache, null, 2)};`,
        );
      // A placeholder left in would break the worker at install: fail the build instead.
      if (/^const (VERSION|PRECACHE) = (__|"__)/m.test(source)) {
        this.error("service worker placeholders were not filled in");
      }
      this.emitFile({ type: "asset", fileName: "sw.js", source });
    },
  };
}

export default defineConfig(({ mode }) => {
  // Where the hub runs in development. The browser only ever talks to Vite, so the dashboard
  // and the API share one origin: no CORS, and stream tickets work exactly as in production.
  const hub = loadEnv(mode, process.cwd(), "").VISION_HUB_URL || "http://127.0.0.1:8000";
  const proxy = {
    "/api": { target: hub, changeOrigin: true, ws: true },
  };

  return {
    plugins: [tailwindcss(), serviceWorker()],
    server: { port: 5173, strictPort: true, proxy },
    preview: { port: 4173, strictPort: true, proxy },
    build: { target: "es2022", sourcemap: true },
  };
});
