import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

declare const process: { env: Record<string, string | undefined> };

// The console is served behind a reverse proxy in production (nginx in front of
// the FastAPI container), so it only ever talks to its own origin.  In
// development the dev server proxies /api to the backend, which keeps the
// browser from ever needing to know a host name.
export default defineConfig({
  plugins: [react()],
  server: {
    host: "0.0.0.0",
    port: 5173,
    strictPort: true,
    // The console is reached through a reverse proxy with a generated host
    // name; Vite 5.4.11 (pinned) performs no host allowlist check, and every
    // request the browser makes is relative to that same origin.
    proxy: {
      "/api": {
        target: process.env.CRIMELINK_API ?? "http://127.0.0.1:8000",
        changeOrigin: true,
        ws: true,
      },
    },
  },
  build: { outDir: "dist", sourcemap: false, chunkSizeWarningLimit: 1200 },
});
