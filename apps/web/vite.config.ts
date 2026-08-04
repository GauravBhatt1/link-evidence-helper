import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig(() => {
  const apiMode = process.env.VITE_SEARCH_TRANSPORT === "api";
  return {
    plugins: [react(), tailwindcss()],
    server: {
      host: "127.0.0.1",
      port: 5173,
      strictPort: true,
      open: false,
      ...(apiMode ? {
        proxy: {
          "/api": {
            target: "http://127.0.0.1:8780",
            changeOrigin: false,
          },
        },
      } : {}),
    },
    preview: {
      host: "127.0.0.1",
      port: 5173,
      strictPort: true,
      open: false,
    },
    test: {
      environment: "jsdom",
      setupFiles: "./src/test/setup.ts",
      include: ["src/**/*.test.{ts,tsx}"],
      css: true,
    },
  };
});
