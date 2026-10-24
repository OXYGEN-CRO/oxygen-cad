import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./tests",
  workers: 1,
  timeout: 60000,
  use: {
    baseURL: process.env.O3D_BROWSER_URL ?? "http://127.0.0.1:8877",
    viewport: { width: 1512, height: 982 },
    headless: true,
    launchOptions: {
      args: [
        "--no-sandbox",
        "--use-gl=angle",
        "--use-angle=swiftshader",
        "--enable-unsafe-swiftshader",
      ],
    },
  },
  ...(process.env.O3D_BROWSER_URL
    ? {}
    : {
        webServer: {
          command: "../.venv/bin/python ../scripts/test_server.py",
          url: "http://127.0.0.1:8877/api/project",
          reuseExistingServer: false,
          timeout: 60000,
        },
      }),
});
