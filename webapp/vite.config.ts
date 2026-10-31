import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev: `npm run dev` (5173) proxies /api to the FastAPI console on :8000.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { "/api": "http://127.0.0.1:8000" } },
  build: { outDir: "dist", sourcemap: false },
  test: { environment: "node" },
});
