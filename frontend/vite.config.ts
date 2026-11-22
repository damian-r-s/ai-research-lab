import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Dev and preview servers proxy /api to the backend, so the browser sees one origin
// (same as production, where nginx proxies /api). SameSite=Strict cookies require this.
const apiTarget = process.env.LAB_API_TARGET ?? "http://127.0.0.1:8000";
const proxy = { "/api": { target: apiTarget, changeOrigin: false } };

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { proxy },
  preview: { proxy },
  build: { sourcemap: false },
});
