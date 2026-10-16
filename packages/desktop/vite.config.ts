import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  // Tauri serves the renderer from a fixed port in development.
  server: { port: 5173, strictPort: true },
  build: { target: "safari15", sourcemap: false },
  test: { environment: "node", globals: true, include: ["src/**/*.test.ts"] },
});
