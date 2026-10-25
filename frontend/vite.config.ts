import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

const backend = `http://127.0.0.1:${process.env.BACKEND_PORT ?? 8000}`;

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      "/api": backend,
      "/stream": backend,
      "/covers": backend,
    },
  },
});
