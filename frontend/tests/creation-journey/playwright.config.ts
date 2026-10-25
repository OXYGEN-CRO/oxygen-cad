import { defineConfig } from "@playwright/test";
import { fileURLToPath } from "node:url";

const frontend = fileURLToPath(new URL("../..", import.meta.url));
const root = fileURLToPath(new URL("../../..", import.meta.url));
const port = process.env.O3D_TEST_PORT ?? "8974";
if (port === "8787") throw new Error("Port 8787 belongs to the user's studio");
const url = `http://127.0.0.1:${port}`;
if (process.env.O3D_BROWSER_URL) {
  const target = new URL(process.env.O3D_BROWSER_URL);
  if (
    process.env.O3D_BROWSER_DISPOSABLE !== "1" ||
    !["127.0.0.1", "localhost"].includes(target.hostname) ||
    !target.port ||
    target.port === "8787"
  ) {
    throw new Error(
      "Creation acceptance requires an explicitly disposable loopback fixture outside port 8787",
    );
  }
}
export default defineConfig({
  testDir: ".",
  testMatch: "creation.spec.ts",
  workers: 1,
  timeout: 120000,
  use: {
    baseURL: process.env.O3D_BROWSER_URL ?? url,
    viewport: { width: 1400, height: 900 },
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
          command: "../.venv/bin/python ../tests/fixtures/creation_server.py",
          cwd: frontend,
          env: { PYTHONPATH: `${root}/src` },
          url: `${url}/api/project`,
          reuseExistingServer: false,
          timeout: 30000,
        },
      }),
});
