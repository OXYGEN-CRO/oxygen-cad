import { test, expect, type Page } from "@playwright/test";
import { readFileSync, writeFileSync } from "node:fs";
import { createHash } from "node:crypto";

async function idle(page: Page) {
  await expect(page.getByLabel("Modeling workspace")).toHaveAttribute(
    "aria-busy",
    "false",
  );
}
async function sketchPosition(page: Page, x: number, y: number) {
  return page.locator(".modeling-canvas-host .modeling-sketch-canvas").evaluate(
    (element, coordinates) => {
      const svg = element as SVGSVGElement;
      const point = svg.createSVGPoint();
      point.x = coordinates[0];
      point.y = -coordinates[1];
      const screen = point.matrixTransform(svg.getScreenCTM()!);
      return { x: screen.x, y: screen.y };
    },
    [x, y],
  );
}
async function sketchPoint(page: Page, x: number, y: number) {
  const position = await sketchPosition(page, x, y);
  await page.mouse.click(position.x, position.y);
}
async function dragSketchPoint(page: Page, from: number[], to: number[]) {
  const start = await sketchPosition(page, from[0], from[1]);
  const end = await sketchPosition(page, to[0], to[1]);
  await page.mouse.move(start.x, start.y);
  await page.mouse.down();
  await page.mouse.move(end.x, end.y, { steps: 6 });
  await page.mouse.up();
  await idle(page);
}

test("a first part is created, dimensioned, drilled, edited and exported through the real App", async ({
  page,
  request,
}, info) => {
  const errors: string[] = [];
  const nativeCommands: { command: string; params: Record<string, any> }[] = [];
  let viewportPoint: number[] | null = null;
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("request", (request) => {
    if (
      request.method() === "POST" &&
      request.url().endsWith("/api/native/execute")
    )
      nativeCommands.push(request.postDataJSON());
    if (request.method() === "POST" && request.url().endsWith("/api/context")) {
      const context = request.postDataJSON();
      if (context.selection?.kind === "face")
        viewportPoint = context.selection.point;
    }
  });
  const blank = await (await request.get("/api/project")).json();
  expect(blank.features).toEqual([]);
  expect(blank.native_document).toBeNull();
  await page.goto("/");
  await expect(page.getByLabel("Workspace", { exact: true })).toHaveValue(
    "modeling",
  );
  const start = page.getByLabel("Start a design", { exact: true });
  await expect(
    start.getByRole("button", { name: "Create sketch", exact: true }),
  ).toBeVisible();
  await expect(
    start.getByRole("button", { name: "Import CAD", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Build model", exact: true }),
  ).toHaveCount(0);
  await page.screenshot({ path: info.outputPath("01-create-first-part.png") });

  await start.getByRole("button", { name: "Import CAD", exact: true }).click();
  await expect(
    page.getByLabel("Import exchange file", { exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Close properties", exact: true })
    .click();
  await start
    .getByRole("button", { name: "Create sketch", exact: true })
    .click();
  await expect(page.getByLabel("Sketch name", { exact: true })).toBeVisible();
  await page
    .getByRole("button", { name: "Close properties", exact: true })
    .click();

  // The blank feature '+' reaches native sketch creation rather than old profile forms.
  await page.getByRole("button", { name: "Add feature", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Add profile", exact: true }),
  ).toHaveCount(0);
  await page.getByLabel("Sketch name", { exact: true }).fill("First plate");
  const renderer = await page.locator(".webgl canvas").elementHandle();
  await page
    .getByRole("button", { name: "Create sketch", exact: true })
    .click();
  await idle(page);
  const canvas = page.locator(".modeling-canvas-host .modeling-sketch-canvas");
  await expect(canvas).toBeVisible();
  await expect(
    page.locator("#inspector-sidebar .modeling-sketch-canvas"),
  ).toHaveCount(0);
  const canvasBox = await canvas.boundingBox();
  expect(canvasBox!.width).toBeGreaterThan(700);
  expect(canvasBox!.height).toBeGreaterThan(600);
  await page
    .getByLabel("Sketch tool", { exact: true })
    .selectOption("rectangle");
  await sketchPoint(page, -20, -15);
  await sketchPoint(page, 20, 15);
  await idle(page);
  await expect(page.locator(".modeling-curve")).toHaveCount(4);
  await page.getByLabel("Sketch tool", { exact: true }).selectOption("select");
  await sketchPoint(page, 0, -15);
  await idle(page);
  await page
    .getByLabel("Dimension type", { exact: true })
    .selectOption("length");
  await page
    .getByLabel("Dimension value", { exact: true })
    .fill("width = 40 mm");
  await page
    .getByRole("button", { name: "Close properties", exact: true })
    .click();
  await expect(canvas).toBeVisible();
  const edge = page.locator('.modeling-curve[data-entity="l1"]');
  const beforeDrag = await edge.getAttribute("d");
  await dragSketchPoint(page, [0, -15], [0, -16]);
  await expect(edge).not.toHaveAttribute("d", beforeDrag!);
  await dragSketchPoint(page, [0, -16], [0, -15]);
  await page
    .getByRole("button", { name: "Expand inspector sidebar", exact: true })
    .click();
  await expect(page.getByLabel("Dimension value", { exact: true })).toHaveValue(
    "width = 40 mm",
  );
  await page
    .getByRole("button", { name: "Add dimension", exact: true })
    .click();
  await idle(page);
  await page
    .getByRole("button", { name: "Auto constrain", exact: true })
    .click();
  await idle(page);
  await expect(
    page.getByText("Fully constrained · 0 DOF", { exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: info.outputPath("02-sketch-in-main-canvas.png"),
  });
  await page
    .getByRole("button", { name: "Finish sketch", exact: true })
    .click();
  await idle(page);
  expect(
    await renderer!.evaluate((element) => element.isConnected),
  ).toBeTruthy();
  await expect(page.locator(".modeling-canvas-host")).toBeHidden();
  await page.getByRole("button", { name: "Add feature", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Sketch", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  await expect(
    page.getByRole("button", { name: "Add profile", exact: true }),
  ).toHaveCount(0);
  await page
    .getByRole("button", { name: "Extrude selected profiles", exact: true })
    .click();
  await page.getByLabel("Distance", { exact: true }).fill("5 mm");
  await page
    .getByRole("button", { name: "Apply extrude", exact: true })
    .click();
  await idle(page);
  await expect(page.getByText("Geometry current", { exact: true })).toBeVisible(
    { timeout: 30000 },
  );

  // A real face pick must keep the tool and the user's diameter/depth draft.
  await page.getByRole("button", { name: "Orient view", exact: true }).click();
  await page.getByRole("menuitem", { name: "Top", exact: true }).click();
  await page
    .getByLabel("Solid operation", { exact: true })
    .selectOption("solid.hole");
  await page.getByLabel("Diameter", { exact: true }).fill("6 mm");
  await page
    .getByLabel("Depth (empty = through all)", { exact: true })
    .fill("3 mm");
  await expect(
    page.getByRole("button", { name: "Apply hole", exact: true }),
  ).toBeDisabled();
  const sceneBox = await page.locator(".webgl canvas").boundingBox();
  await page.mouse.click(
    sceneBox!.x + sceneBox!.width / 2 + 30,
    sceneBox!.y + sceneBox!.height / 2 + 20,
  );
  await expect(
    page.getByLabel("Selected hole point", { exact: true }),
  ).toBeVisible();
  await expect.poll(() => viewportPoint).not.toBeNull();
  const pickedPoint = [...viewportPoint!];
  expect(Math.abs(pickedPoint[0])).toBeGreaterThan(1);
  await expect(page.getByLabel("Workspace", { exact: true })).toHaveValue(
    "modeling",
  );
  await expect(page.getByLabel("Solid operation", { exact: true })).toHaveValue(
    "solid.hole",
  );
  await expect(page.getByLabel("Diameter", { exact: true })).toHaveValue(
    "6 mm",
  );
  await expect(
    page.getByLabel("Depth (empty = through all)", { exact: true }),
  ).toHaveValue("3 mm");
  await expect(
    page.getByLabel("Position on face", { exact: true }),
  ).toHaveCount(0);
  await page.screenshot({
    path: info.outputPath("04-face-pick-preserves-hole-draft.png"),
  });
  await page.getByRole("button", { name: "Apply hole", exact: true }).click();
  await idle(page);
  const drilled = nativeCommands.find(
    (command) => command.command === "solid.hole",
  )!;
  expect(drilled.params.position).toHaveLength(3);
  expect(drilled.params.position).toEqual(pickedPoint);
  expect(drilled.params.position[2]).toBeCloseTo(5, 3);
  expect(drilled.params.diameter).toBe("6 mm");
  expect(drilled.params.depth).toBe("3 mm");
  await page.getByRole("button", { name: /^Hole1/ }).click();
  await page.getByLabel("Feature diameter", { exact: true }).fill("8 mm");
  await page
    .getByRole("button", { name: "Save feature definition", exact: true })
    .click();
  await idle(page);
  const saved = await (await request.get("/api/project")).json();
  await expect(
    page.getByLabel(`Saved revision ${saved.revision}`, { exact: true }),
  ).toBeVisible();
  await page.reload();
  await page.getByRole("button", { name: /^Hole1/ }).click();
  await expect(
    page.getByLabel("Feature diameter", { exact: true }),
  ).toHaveValue("8 mm");
  await expect(page.getByLabel("Feature depth", { exact: true })).toHaveValue(
    "3 mm",
  );
  expect(
    (await (await request.get("/api/project")).json()).native_document,
  ).toEqual(saved.native_document);
  await expect(page.getByText("Geometry current", { exact: true })).toBeVisible(
    { timeout: 30000 },
  );
  await page.screenshot({
    path: info.outputPath("03-edited-part-reopened.png"),
  });

  await page.getByLabel("Workspace", { exact: true }).selectOption("interop");
  await page.getByLabel("Export format", { exact: true }).selectOption("step");
  await page
    .getByRole("button", { name: "Export document", exact: true })
    .click();
  const link = page.getByRole("link", { name: "Download STEP", exact: true });
  await expect(link).toBeVisible({ timeout: 30000 });
  const downloadEvent = page.waitForEvent("download");
  await link.click();
  const download = await downloadEvent;
  const step = info.outputPath("first-part.step");
  await download.saveAs(step);
  expect(readFileSync(step, "utf8")).toContain("ISO-10303-21");
  const artifact = new URL(
    (await link.getAttribute("href"))!,
    page.url(),
  ).pathname.split("/api/artifacts/")[1];
  const verification = await request.get(
    `/api/test/creation/step?artifact=${encodeURIComponent(artifact)}`,
  );
  expect(verification.ok(), await verification.text()).toBeTruthy();
  const measured = await verification.json();
  expect(measured.sha256).toBe(
    createHash("sha256").update(readFileSync(step)).digest("hex"),
  );
  expect(measured.valid).toBeTruthy();
  expect(measured.solids).toBe(1);
  expect(measured.volume_mm3).toBeCloseTo(40 * 30 * 5 - Math.PI * 4 * 4 * 3, 2);
  const bounds = measured.bounds as number[];
  expect(bounds[3] - bounds[0]).toBeCloseTo(40, 4);
  expect(bounds[4] - bounds[1]).toBeCloseTo(30, 4);
  expect(bounds[5] - bounds[2]).toBeCloseTo(5, 4);
  const removedVolume = Math.PI * 4 * 4 * 3;
  expect(measured.center_of_mass_mm[0]).toBeCloseTo(
    (-removedVolume * pickedPoint[0]) / measured.volume_mm3,
    4,
  );
  expect(measured.center_of_mass_mm[1]).toBeCloseTo(
    (-removedVolume * pickedPoint[1]) / measured.volume_mm3,
    4,
  );
  expect(measured.center_of_mass_mm[2]).toBeCloseTo(
    (6000 * 2.5 - removedVolume * 3.5) / measured.volume_mm3,
    4,
  );
  writeFileSync(
    info.outputPath("independent-step-measurements.json"),
    JSON.stringify(measured, null, 2),
  );

  // A missing viewport scene must not hide manual rebuild recovery for a saved solid.
  await page.route("**/api/native/scene?*", (route) =>
    route.fulfill({ status: 503, json: { detail: "Scene temporarily unavailable" } }),
  );
  await page.route("**/api/state", async (route) => {
    const response = await route.fetch();
    const state = await response.json();
    await route.fulfill({ response, json: { ...state, geometry_state: "stale" } });
  });
  await page.reload();
  await expect(page.getByRole("alert")).toContainText("Scene temporarily unavailable");
  const rebuild = page.getByRole("button", { name: "Build model", exact: true });
  await expect(rebuild).toBeEnabled();
  const buildResponse = page.waitForResponse((response) =>
    response.request().method() === "POST" && response.url().endsWith("/api/jobs/build"),
  );
  await rebuild.click();
  expect((await buildResponse).ok()).toBeTruthy();
  await page.unroute("**/api/state");
  await page.unroute("**/api/native/scene?*");
  await page.reload();
  await expect(page.getByText("Geometry current", { exact: true })).toBeVisible({ timeout: 30000 });
  expect((await (await request.get("/api/project")).json()).native_document).toEqual(saved.native_document);
  expect(errors).toEqual([]);
});
