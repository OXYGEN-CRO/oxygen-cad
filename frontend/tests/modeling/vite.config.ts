import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
const port = process.env.O3D_TEST_PORT ?? "8964";
const api = process.env.O3D_BROWSER_URL ?? `http://127.0.0.1:${port}`;
export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5194,
    strictPort: true,
    proxy: {
      "/api": { target: api, changeOrigin: true, headers: { origin: api } },
    },
  },
});
