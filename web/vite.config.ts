import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Vite dev server runs on :5173 during development; aiohttp serves the built
// bundle in production. The /api proxy keeps the browser talking to a single
// origin so SSE and ETag caching behave identically in both modes.
//
// Vitest config lives in `vitest.config.ts` — keeping it separate avoids the
// vite v5/v6 plugin-type collision pulled in via vitest's transitive deps.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": "http://127.0.0.1:8765",
      "/healthz": "http://127.0.0.1:8765",
      "/state": "http://127.0.0.1:8765",
    },
  },
  build: {
    outDir: "dist",
    sourcemap: true,
  },
});
