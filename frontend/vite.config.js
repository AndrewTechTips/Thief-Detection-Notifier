/// <reference types="node" />

import tailwindcss from "@tailwindcss/vite";
import { defineConfig, loadEnv } from "vite";

export default defineConfig(({ mode }) => {
  // Where the hub runs in development. The browser only ever talks to Vite, so the dashboard
  // and the API share one origin: no CORS, and stream tickets work exactly as in production.
  const hub = loadEnv(mode, process.cwd(), "").VISION_HUB_URL || "http://127.0.0.1:8000";
  const proxy = {
    "/api": { target: hub, changeOrigin: true, ws: true },
  };

  return {
    plugins: [tailwindcss()],
    server: { port: 5173, strictPort: true, proxy },
    preview: { port: 4173, strictPort: true, proxy },
    build: { target: "es2022", sourcemap: true },
  };
});
