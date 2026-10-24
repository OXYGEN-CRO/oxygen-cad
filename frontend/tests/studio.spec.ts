import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";

test("native editing, revision persistence, checks, context and release", async ({
  page,
  request,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/");
  await expect(
    page.getByText("Geometry current", { exact: true }),
  ).toBeVisible();
  await expect(page.locator("canvas")).toBeVisible();
  await page
    .getByRole("button", { name: "Mounting plate", exact: false })
    .click();
  await expect(page.getByLabel("Width", { exact: true })).toHaveValue(
    "$length",
  );
  await page.getByLabel("Width", { exact: true }).fill("64");
  await page.getByRole("button", { name: "Save changes" }).click();
  await expect
    .poll(async () =>
      (await request.get("/api/project"))
        .json()
        .then((p) => p.features[0].width),
    )
    .toBe(64);
  await expect(
    page.getByText("Showing previous geometry · rebuild required"),
  ).toBeVisible();
  await page.getByRole("button", { name: "Build model", exact: true }).click();
  await expect(page.getByText("Geometry current", { exact: true })).toBeVisible(
    { timeout: 30000 },
  );
  await page.getByRole("button", { name: "Check fit", exact: true }).click();
  await expect(
    page.getByText("One valid connected solid", { exact: true }),
  ).toBeVisible({ timeout: 30000 });
  // Pick an actual rendered face, then verify the agent sees the selected geometry.
  const canvas = page.locator("canvas");
  const box = await canvas.boundingBox();
  if (!box) throw new Error("Canvas missing");
  for (const offset of [
    [0, 0],
    [20, 20],
    [-20, 20],
    [20, -20],
    [-50, 0],
    [0, 50],
  ]) {
    await page.mouse.click(
      box.x + box.width / 2 + offset[0],
      box.y + box.height / 2 + offset[1],
    );
    const state = await (await request.get("/api/state")).json();
    if (state.context.selection) break;
  }
  const state = await (await request.get("/api/state")).json();
  expect(state.context.selection?.face).toBeTruthy();
  await page.getByRole("button", { name: "Agent tools", exact: true }).click();
  await expect(
    page.getByText("Your agent works on the same project."),
  ).toBeVisible();
  await page.getByRole("button", { name: "Export", exact: true }).click();
  await expect(page.getByRole("link", { name: /Download r/ })).toBeVisible({
    timeout: 30000,
  });
  const href = await page
    .getByRole("link", { name: /Download r/ })
    .getAttribute("href");
  const exported = await request.get(href!);
  expect(exported.status()).toBe(200);
  expect((await exported.body()).byteLength).toBeGreaterThan(1000);
  await page.screenshot({
    path: "test-results/studio-design.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "Verify", exact: true }).click();
  await expect(page.getByText("Static stress", { exact: true })).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Run static study" }),
  ).toBeDisabled();
  await page.getByRole("button", { name: "History", exact: true }).click();
  await expect(page.getByText("Every change, recorded.")).toBeVisible();
  await page.getByRole("button", { name: "Source", exact: true }).click();
  const source = page.getByLabel("Project JSON");
  const document = JSON.parse(await source.inputValue());
  document.name = "Source-edited mounting bracket";
  await source.fill(JSON.stringify(document, null, 2));
  await page.waitForTimeout(2000);
  expect(JSON.parse(await source.inputValue()).name).toBe(document.name);
  await page.getByRole("button", { name: "Apply source document" }).click();
  await expect(page.getByLabel("Project name")).toHaveValue(document.name);
  await page.reload();
  await expect(page.getByLabel("Project name")).toHaveValue(document.name);
  expect(errors).toEqual([]);
});

test("a concurrent agent edit cannot be overwritten by an old manual draft", async ({
  page,
  request,
}) => {
  await page.goto("/");
  await page
    .getByRole("button", { name: "Mounting plate", exact: false })
    .click();
  await page.getByLabel("Width", { exact: true }).fill("66");
  const p = await (await request.get("/api/project")).json();
  const revision = p.revision;
  p.name = "Agent changed name";
  expect(
    (
      await request.post("/api/commands", {
        data: { expected_revision: revision, actor: "agent", project: p },
      })
    ).ok(),
  ).toBeTruthy();
  await page.waitForTimeout(2000);
  await page.getByRole("button", { name: "Save changes" }).click();
  await expect(page.getByRole("alert")).toContainText("Revision conflict");
  const current = await (await request.get("/api/project")).json();
  expect(current.name).toBe("Agent changed name");
  expect(current.features[0].width).not.toBe(66);
});

test("a real study runs through the browser and exposes current field results", async ({
  page,
  request,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const current = await (await request.get("/api/project")).json();
  const fixture = JSON.parse(
    readFileSync("../examples/cantilever.json", "utf8"),
  );
  fixture.revision = current.revision;
  expect(
    (
      await request.post("/api/commands", {
        data: {
          expected_revision: current.revision,
          actor: "human",
          project: fixture,
        },
      })
    ).ok(),
  ).toBeTruthy();
  await page.goto("/");
  await page.getByRole("button", { name: "Build model", exact: true }).click();
  await expect(page.getByText("Geometry current", { exact: true })).toBeVisible(
    { timeout: 30000 },
  );
  await page.getByRole("button", { name: "Verify", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Run static study" }),
  ).toBeEnabled();
  await page.getByRole("button", { name: "Run static study" }).click();
  await expect(
    page.getByText("Maximum displacement", { exact: true }),
  ).toBeVisible({ timeout: 45000 });
  await page.getByRole("button", { name: "View stress", exact: true }).click();
  await expect(page.getByText("von Mises stress · MPa")).toBeVisible();
  await expect(page.locator(".webgl")).toHaveAttribute(
    "data-field-ready",
    "true",
  );
  await page.getByRole("button", { name: "Fit model", exact: true }).click();
  await page.screenshot({
    path: "test-results/studio-stress.png",
    fullPage: true,
  });
  const state = await (await request.get("/api/state")).json();
  expect(state.analysis.converged).toBeTruthy();
  expect(state.analysis.max_displacement_mm).toBeGreaterThan(0.152);
  expect(state.analysis.max_displacement_mm).toBeLessThan(0.168);
  expect(errors).toEqual([]);
});
