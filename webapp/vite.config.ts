import { defineConfig } from "vite";

export default defineConfig({
  base: "./",
  build: { outDir: "dist", target: "es2020", assetsInlineLimit: 0 },
  server: { proxy: { "/api": "http://localhost:8000" } },
});
