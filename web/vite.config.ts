import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Deployed under https://huang-yu-chen.github.io/fifth-estate/ — relative base + HashRouter keeps it portable.
export default defineConfig({
  base: "./",
  plugins: [react()],
  build: { chunkSizeWarningLimit: 1200 },
});
