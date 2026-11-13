/// <reference types="vitest" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import { VitePWA } from "vite-plugin-pwa";

// Same-origin in dev via proxy, so the SameSite=Strict refresh cookie works (doc 10/12).
export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      registerType: "autoUpdate",
      manifest: { name: "SaidSigned", short_name: "SaidSigned", theme_color: "#1e293b", background_color: "#ffffff",
                  display: "standalone", start_url: "/", icons: [] },
      workbox: {
        // App shell only. Never cache API, WebSocket or media responses (doc 10 §8, doc 12 §B.2).
        globPatterns: ["**/*.{js,css,html,svg,woff2}"],
        navigateFallbackDenylist: [/^\/api\//, /^\/ws\//],
        runtimeCaching: [],
      },
    }),
  ],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://localhost:8000", changeOrigin: false },
      "/ws": { target: "ws://localhost:8000", ws: true },
    },
  },
  test: { environment: "jsdom", globals: false },
});
