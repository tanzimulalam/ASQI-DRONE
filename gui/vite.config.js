import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Build outputs to gui/dist/, which the ground bridge serves as a static SPA.
// In dev (`npm run dev`), Vite serves on :5173 and proxies the live data paths
// to the running ground bridge on :8000, so the WebSocket + health checks work
// against real hardware without CORS or a separate build.
const BRIDGE = process.env.BRIDGE_ORIGIN || "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  base: "./",
  build: {
    outDir: "dist",
    emptyOutDir: true,
    target: "es2020",
  },
  server: {
    host: true,
    port: 5173,
    proxy: {
      "/ws": { target: BRIDGE.replace(/^http/, "ws"), ws: true },
      "/healthz": { target: BRIDGE },
      "/readyz": { target: BRIDGE },
      "/camera": { target: BRIDGE },
    },
  },
});
