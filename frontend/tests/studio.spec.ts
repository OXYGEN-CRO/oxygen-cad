import { test, expect, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";

async function openTool(page: Page, name: string) {
  await page
    .getByRole("button", { name: "Project tools", exact: true })
    .click();
  await page
    .getByRole("menu", { name: "Project tools", exact: true })
    .getByRole("menuitem", { name, exact: true })
    .click();
}

test("minimal studio keeps editing tools accessible across screen sizes", async ({
  page,
  request,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const before = await (await request.get("/api/project")).json();
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/");
  await expect(page.locator("canvas")).toBeVisible();
  await expect(page.locator(".right-panel")).toHaveCount(0);
  await expect(page.locator(".verification-panel")).toHaveCount(0);
  const viewport = await page.locator("canvas").boundingBox();
  expect(viewport!.width * viewport!.height).toBeGreaterThan(1512 * 982 * 0.7);
  for (const width of [320, 390, 768, 1024, 1512]) {
    await page.setViewportSize({ width, height: 982 });
    for (const view of ["Model", "Study setup", "History", "Source"]) {
      if (view === "Model") {
        const close = page.getByRole("button", {
          name: "Collapse inspector sidebar",
          exact: true,
        });
        if (await close.isVisible()) await close.click();
        await page
          .getByRole("button", { name: "Mounting plate", exact: false })
          .click();
      } else await openTool(page, view);
      const appearance = await page.evaluate(() => {
        const rgb = (value: string) =>
          value
            .match(/[\d.]+/g)!
            .slice(0, 3)
            .map(Number);
        const left = getComputedStyle(document.querySelector(".canvas-panel")!);
        const input = getComputedStyle(document.querySelector("input")!);
        return {
          scheme: getComputedStyle(document.documentElement).colorScheme,
          inputBackground: rgb(input.backgroundColor),
          inputText: rgb(input.color),
          radius: parseFloat(left.borderRadius),
          overflow: document.documentElement.scrollWidth > innerWidth,
        };
      });
      expect(appearance.scheme).toBe("dark");
      expect(
        appearance.inputBackground.every((channel) => channel < 60),
      ).toBeTruthy();
      expect(
        appearance.inputText.every((channel) => channel > 150),
      ).toBeTruthy();
      expect(appearance.radius).toBeGreaterThanOrEqual(12);
      expect(
        appearance.overflow,
        `${view} overflows at ${width}px`,
      ).toBeFalsy();
    }
  }
  await page.setViewportSize({ width: 390, height: 844 });
  await openTool(page, "Agent tools");
  await expect(page.getByText("Agent commands", { exact: true })).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
  await openTool(page, "Source");
  await page.getByLabel("Project JSON").scrollIntoViewIfNeeded();
  await expect(page.getByLabel("Project JSON")).toBeVisible();
  await page.screenshot({
    path: "test-results/studio-mobile.png",
    fullPage: true,
  });
  const after = await (await request.get("/api/project")).json();
  expect(after).toEqual(before);
  expect(errors).toEqual([]);
});

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
  const closeInspector = page.getByRole("button", {
    name: "Collapse inspector sidebar",
    exact: true,
  });
  if (await closeInspector.isVisible()) await closeInspector.click();
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
  await openTool(page, "Study setup");
  await page.getByRole("button", { name: "Check fit", exact: true }).click();
  await expect(
    page.getByText("One valid connected solid", { exact: true }),
  ).toBeVisible({ timeout: 30000 });
  await page
    .getByRole("button", { name: "Close properties", exact: true })
    .click();
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
  await openTool(page, "Agent tools");
  await expect(page.getByText("Agent commands", { exact: true })).toBeVisible();
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
  await openTool(page, "Study setup");
  await expect(
    page.getByRole("heading", { name: "Static stress", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Run static study" }),
  ).toBeDisabled();
  await openTool(page, "History");
  await expect(page.getByText("Revision 1", { exact: true })).toBeVisible();
  await openTool(page, "Source");
  const source = page.getByLabel("Project JSON");
  const document = JSON.parse(await source.inputValue());
  document.name = "Source-edited mounting bracket";
  await source.fill(JSON.stringify(document, null, 2));
  await page.waitForTimeout(2000);
  expect(JSON.parse(await source.inputValue()).name).toBe(document.name);
  await page.getByRole("button", { name: "Apply source document" }).click();
  await openTool(page, "Project settings");
  await expect(page.getByLabel("Project name")).toHaveValue(document.name);
  await page.reload();
  await openTool(page, "Project settings");
  await expect(page.getByLabel("Project name")).toHaveValue(document.name);
  expect(errors).toEqual([]);
});

test("a concurrent agent edit cannot be overwritten by an old manual draft", async ({
  page,
  request,
}) => {
  await page.goto("/");
  const closeInspector = page.getByRole("button", {
    name: "Collapse inspector sidebar",
    exact: true,
  });
  if (await closeInspector.isVisible()) await closeInspector.click();
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
  await expect(page.locator("canvas")).toBeVisible();
  const build = page.getByRole("button", { name: "Build model", exact: true });
  if (await build.isVisible()) await build.click();
  await expect(page.getByText("Geometry current", { exact: true })).toBeVisible(
    { timeout: 30000 },
  );
  await openTool(page, "Study setup");
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

test("native Rust features remain editable through the same studio document", async ({
  page,
  request,
}) => {
  const current = await (await request.get("/api/project")).json();
  const fixture = {
    revision: current.revision,
    name: "Rust enclosure",
    geometry_engine: "solidcraft",
    features: [
      {
        id: "block",
        name: "Block",
        type: "native",
        command: "solid.box",
        params: { length: 30, width: 20, height: 10 },
      },
      {
        id: "shell",
        name: "Shell",
        type: "native",
        command: "solid.shell",
        params: { faces: [[15, 10, 10]], thickness: 2 },
      },
    ],
  };
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
  await page.getByRole("button", { name: "Shell", exact: false }).click();
  await expect(page.getByLabel("Native command")).toHaveValue("solid.shell");
  await page
    .getByLabel("Native arguments")
    .fill(JSON.stringify({ faces: [[15, 10, 10]], thickness: 1.5 }));
  await page.getByLabel("Native command").click();
  await page.getByRole("button", { name: "Save changes" }).click();
  await expect
    .poll(
      async () =>
        (await (await request.get("/api/project")).json()).features[1].params
          .thickness,
    )
    .toBe(1.5);
  await page.getByRole("button", { name: "Build model", exact: true }).click();
  await expect(page.getByText("Geometry current", { exact: true })).toBeVisible(
    { timeout: 30000 },
  );
  const state = await (await request.get("/api/state")).json();
  expect(state.build.engine.engine).toBe("solidcraft");
  expect(state.build.volume_mm3).toBeCloseTo(6000 - 27 * 17 * 8.5, 4);
});
