/// <reference types="node" />

import { createHash } from "node:crypto";
import { cp, readFile, stat, writeFile } from "node:fs/promises";
import { join, normalize } from "node:path";

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

/**
 * The public demo (AD-24): serves and publishes the hub's recording (`vision-hub export-demo`,
 * in .demo/) at <base>/demo/, and adds a 404.html copy of the app, so GitHub Pages answers deep
 * links ("/iot-vision-hub/events") with the dashboard instead of an error.
 * @returns {import("vite").Plugin}
 */
function demoRecording() {
  const source = new URL("./.demo/", import.meta.url).pathname;
  /** @type {import("vite").ResolvedConfig} */
  let config;
  return {
    name: "vision-hub:demo-recording",
    configResolved(resolved) {
      config = resolved;
    },
    configureServer(server) {
      const prefix = `${server.config.base}demo/`;
      server.middlewares.use(async (request, response, next) => {
        const path = request.url?.split("?")[0] ?? "";
        if (!path.startsWith(prefix)) return next();
        const file = normalize(join(source, decodeURIComponent(path.slice(prefix.length))));
        if (!file.startsWith(source) || !(await stat(file).catch(() => null))?.isFile())
          return next();
        response.setHeader(
          "Content-Type",
          file.endsWith(".json")
            ? "application/json"
            : file.endsWith(".jpg")
              ? "image/jpeg"
              : "video/webm",
        );
        response.end(await readFile(file));
      });
    },
    async writeBundle() {
      const out = config.build.outDir;
      if (!(await stat(join(source, "manifest.json")).catch(() => null))) {
        this.error("no demo recording: run `uv run vision-hub export-demo` first");
      }
      await cp(source, join(out, "demo"), { recursive: true });
      await writeFile(join(out, "404.html"), await readFile(join(out, "index.html")));
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

  if (mode === "demo") {
    // Served from a subdirectory (GitHub Pages: /<repository>/), with no hub behind it.
    return {
      base: process.env.DEMO_BASE || "/iot-vision-hub/",
      plugins: [tailwindcss(), demoRecording()],
      server: { port: 5174, strictPort: true },
      preview: { port: 4174, strictPort: true },
      build: { target: "es2022", outDir: "dist-demo", sourcemap: true },
    };
  }

  return {
    plugins: [tailwindcss(), serviceWorker()],
    server: { port: 5173, strictPort: true, proxy },
    preview: { port: 4173, strictPort: true, proxy },
    build: { target: "es2022", sourcemap: true },
  };
});
