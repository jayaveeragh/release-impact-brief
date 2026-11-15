import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In development the UI runs on Vite's server and forwards /api to `rib serve`.
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://127.0.0.1:8765" } },
});
