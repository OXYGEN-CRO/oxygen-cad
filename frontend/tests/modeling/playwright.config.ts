import { defineConfig } from "@playwright/test";
import { fileURLToPath } from "node:url";
const frontend = fileURLToPath(new URL("../..", import.meta.url));
if (process.env.O3D_TEST_PORT === "8787") {
  throw new Error("Port 8787 is reserved for the user's studio");
}
if (process.env.O3D_BROWSER_URL) {
  const target = new URL(process.env.O3D_BROWSER_URL);
  if (
    process.env.O3D_BROWSER_DISPOSABLE !== "1" ||
    !["127.0.0.1", "localhost"].includes(target.hostname) ||
    target.port === "8787" ||
    !target.port
  ) {
    throw new Error(
      "Modeling tests require an explicitly disposable loopback backend outside port 8787",
    );
  }
}
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
    ...(process.env.O3D_BROWSER_URL
      ? []
      : [
          {
            command: "../.venv/bin/python tests/modeling/server.py",
            cwd: frontend,
            url: `http://127.0.0.1:${process.env.O3D_TEST_PORT ?? "8964"}/api/project`,
            timeout: 30000,
            reuseExistingServer: false,
          },
        ]),
    {
      command: "npx vite --config tests/modeling/vite.config.ts",
      cwd: frontend,
      url: "http://127.0.0.1:5194/tests/modeling/fixture.html",
      timeout: 30000,
      reuseExistingServer: false,
    },
  ],
});
