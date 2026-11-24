import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The built interface is served by the GrepLens back end (backend/greplens/static).
export default defineConfig({
  plugins: [react()],
  build: { outDir: "../backend/greplens/static", emptyOutDir: true },
  server: { port: 5173, proxy: { "/api": "http://127.0.0.1:8710" } },
});
