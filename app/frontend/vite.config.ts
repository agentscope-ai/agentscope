import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/canvas": "http://localhost:8000",
      "/relations": "http://localhost:8000",
      "/review": "http://localhost:8000",
      "/audit": "http://localhost:8000",
      "/metrics": "http://localhost:8000"
    }
  }
});
