import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { resolve } from "node:path";

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: resolve(import.meta.dirname, "../src/loro/webui/static"),
    emptyOutDir: true,
  },
  server: {
    proxy: { "/api": "http://127.0.0.1:8765" },
  },
  test: {
    environment: "jsdom",
    setupFiles: "./src/test-setup.ts",
  },
});
