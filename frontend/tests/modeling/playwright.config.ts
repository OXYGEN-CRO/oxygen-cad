import { defineConfig } from "@playwright/test";
import { fileURLToPath } from "node:url";
const frontend = fileURLToPath(new URL("../..", import.meta.url));
export default defineConfig({
  testDir: ".",
  testMatch: "modeling.spec.ts",
  workers: 1,
  timeout: 120000,
  use: {
    baseURL: "http://127.0.0.1:5194",
    viewport: { width: 1120, height: 1050 },
    headless: true,
    launchOptions: { args: ["--no-sandbox"] },
  },
  webServer: [
    {
      command: "../.venv/bin/python tests/modeling/server.py",
      cwd: frontend,
      url: `http://127.0.0.1:${process.env.O3D_TEST_PORT ?? "8964"}/api/project`,
      timeout: 30000,
      reuseExistingServer: false,
    },
    {
      command: "npx vite --config tests/modeling/vite.config.ts",
      cwd: frontend,
      url: "http://127.0.0.1:5194/tests/modeling/fixture.html",
      timeout: 30000,
      reuseExistingServer: false,
    },
  ],
});
