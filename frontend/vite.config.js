import path from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const rootDir = path.dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  plugins: [react()],
  envDir: path.resolve(rootDir, ".."),
  server: {
    host: "localhost",
    port: 5174,
    strictPort: true,
    proxy: {
      "/api": "http://localhost:8000",
    },
  },
});
