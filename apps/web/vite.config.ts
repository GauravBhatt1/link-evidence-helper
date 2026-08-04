import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vitest/config";

const loopbackHosts = new Set(["127.0.0.1", "localhost", "::1", "[::1]"]);

function validatedApiTarget(value: string | undefined) {
  const target = value || "http://127.0.0.1:8780";
  const parsed = new URL(target);
  if (parsed.protocol !== "http:" || !loopbackHosts.has(parsed.hostname)) {
    throw new Error("VITE_SEARCH_API_TARGET must be an HTTP loopback URL");
  }
  return parsed.origin;
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, ".", "");
  const apiMode = env.VITE_SEARCH_TRANSPORT === "api";
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
            target: validatedApiTarget(env.VITE_SEARCH_API_TARGET),
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
