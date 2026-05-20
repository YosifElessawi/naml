import { defineConfig } from "vitest/config";

// Vitest config is intentionally separate from vite.config.ts: vitest@2.x
// ships vite@5 typings as a transitive dep, which collides with the vite@6
// types pulled into vite.config.ts. Vitest reads vitest.config.ts in
// preference to vite.config.ts, so this is the source of truth for tests.
//
// No @vitejs/plugin-react here — Vite/Vitest's esbuild transform handles the
// `react-jsx` automatic runtime for .tsx test sources without needing the
// dev-only plugin (which would re-introduce the v5/v6 plugin type clash).
export default defineConfig({
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
  },
});
