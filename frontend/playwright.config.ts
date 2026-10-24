import { defineConfig } from "@playwright/test";
const fixtureUrl = `http://127.0.0.1:${process.env.O3D_TEST_PORT ?? "8877"}`;
if (process.env.O3D_BROWSER_URL) {
  const target = new URL(process.env.O3D_BROWSER_URL);
  if (
    process.env.O3D_BROWSER_DISPOSABLE !== "1" ||
    !["127.0.0.1", "localhost"].includes(target.hostname) ||
    target.port === "8787" ||
    !target.port
  ) {
    throw new Error(
      "External browser tests require O3D_BROWSER_DISPOSABLE=1 and an isolated loopback URL outside port 8787",
    );
  }
}
if (new URL(fixtureUrl).port === "8787") {
  throw new Error("Port 8787 is reserved for the user's studio");
}
export default defineConfig({
  testDir: "./tests",
  testIgnore: ["**/modeling/**", "**/simulation-domain.spec.ts"],
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
