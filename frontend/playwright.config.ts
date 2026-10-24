import { defineConfig } from "@playwright/test";
const fixtureUrl = `http://127.0.0.1:${process.env.O3D_TEST_PORT ?? "8877"}`;
export default defineConfig({
  testDir: "./tests",
  testIgnore: ["**/modeling/**"],
  workers: 1,
  timeout: 60000,
  use: {
    baseURL: process.env.O3D_BROWSER_URL ?? fixtureUrl,
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
          url: `${fixtureUrl}/api/project`,
          reuseExistingServer: false,
          timeout: 60000,
        },
      }),
});
