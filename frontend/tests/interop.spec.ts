import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import { beginOperation } from "../../tests/browser_operations.cjs";

async function project(request: APIRequestContext) {
  const response = await request.get("/api/project");
  expect(response.ok()).toBeTruthy();
  return response.json();
}

async function blankProject(request: APIRequestContext, name: string) {
  const before = await project(request);
  const response = await request.post("/api/commands", { data: {
    expected_revision: before.revision, actor: "human", action: "replace",
    project: { ...before, name, geometry_engine: "solidcraft", native_document: null,
      domains: {}, features: [], references: [], parameters: {}, requirements: [], material: null,
      placement: { translation: [0, 0, 0], rotation: [0, 0, 0] } },
    summary: "Disposable browser interop fixture",
  } });
  expect(response.ok(), await response.text()).toBeTruthy();
}

async function inspection(request: APIRequestContext) {
  const response = await request.get("/api/native/inspect");
  expect(response.ok(), await response.text()).toBeTruthy();
  return response.json();
}

async function applyUi(page: Page, request: APIRequestContext, button: string) {
  const before = await project(request);
  await page.getByRole("button", { name: button, exact: true }).click();
  await expect.poll(async () => (await project(request)).revision, { timeout: 30000 }).toBe(before.revision + 1);
  await expect(page.getByRole("button", { name: button, exact: true })).toBeEnabled();
}

async function uploadUi(page: Page, request: APIRequestContext, name: string, buffer: Buffer) {
  const before = await project(request);
  await page.getByLabel("Import exchange file", { exact: true }).setInputFiles({ name, mimeType: "application/octet-stream", buffer });
  await expect.poll(async () => (await project(request)).revision, { timeout: 30000 }).toBe(before.revision + 1);
  await expect(page.getByText("Running geometry operation…", { exact: true })).toBeHidden();
}

async function currentGeometry(page: Page, request: APIRequestContext) {
  await expect.poll(async () => (await (await request.get("/api/state")).json()).geometry_state, { timeout: 30000 }).toBe("current");
  await expect(page.getByText("Geometry current", { exact: true })).toBeVisible({ timeout: 10000 });
  await page.getByRole("button", { name: "Fit model", exact: true }).click();
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
}

function inchBoxStl() {
  const vertices = [[0, 0, 0], [1, 0, 0], [1, 2, 0], [0, 2, 0], [0, 0, 3], [1, 0, 3], [1, 2, 3], [0, 2, 3]];
  const triangles = [[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7], [0, 1, 5], [0, 5, 4], [1, 2, 6], [1, 6, 5], [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7]];
  return Buffer.from("solid inch_box\n" + triangles.map(face => "facet normal 0 0 0\nouter loop\n" + face.map(index => `vertex ${vertices[index].join(" ")}\n`).join("") + "endloop\nendfacet\n").join("") + "endsolid inch_box\n");
}

function outlineDxf() {
  const lines = [[[0, 0], [10, 0]], [[10, 0], [10, 20]], [[10, 20], [0, 20]], [[0, 20], [0, 0]]];
  return Buffer.from("0\nSECTION\n2\nHEADER\n9\n$INSUNITS\n70\n4\n0\nENDSEC\n0\nSECTION\n2\nENTITIES\n" + lines.map(([a, b]) => `0\nLINE\n8\n0\n10\n${a[0]}\n20\n${a[1]}\n11\n${b[0]}\n21\n${b[1]}\n`).join("") + "0\nENDSEC\n0\nEOF\n");
}

test("inch mesh import, measured diagnostics, actual OBJ export and cold reopen", async ({ page, request }, testInfo) => {
  test.setTimeout(180000);
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  page.on("console", message => { if (message.type() === "error") errors.push(message.text()); });
  await blankProject(request, "Browser inch mesh acceptance");
  await page.goto("/");
  await page.getByLabel("Workspace", { exact: true }).selectOption("interop");
  await expect(page.getByRole("heading", { name: "Import editable geometry" })).toBeVisible();
  await page.getByLabel("Source units", { exact: true }).selectOption("in");
  await uploadUi(page, request, "inch-box.stl", inchBoxStl());
  const saved = await project(request);
  expect(saved.native_document).not.toBeNull();
  const source = saved.domains.interop.assets.find((asset: any) => asset.source_units === "in");
  const normalized = saved.domains.interop.assets.find((asset: any) => asset.source_asset === source.id);
  expect(normalized.source_units).toBe("mm");
  const native = await inspection(request);
  const bounds = native.inspection.bodies[0].bbox;
  bounds.max.forEach((value: number, axis: number) => expect(value - bounds.min[axis]).toBeCloseTo([25.4, 50.8, 76.2][axis], 4));
  await page.getByRole("tab", { name: "Meshes", exact: true }).click();
  await page.getByLabel("Source mesh", { exact: true }).selectOption(normalized.id);
  const topology = await beginOperation(page, request, "Topology diagnostics", 60000, testInfo.outputDir);
  await page.getByRole("button", { name: "Analyze topology", exact: true }).click();
  await topology(async () => {
    try { return JSON.parse(await page.locator(".interop-result pre").innerText()).diagnostics?.watertight; }
    catch { return false; }
  });
  const diagnostics = JSON.parse(await page.locator(".interop-result pre").innerText()).diagnostics;
  expect(diagnostics.watertight).toBe(true);
  expect(diagnostics.triangles).toBe(12);
  expect(diagnostics.boundary_edges).toBe(0);
  expect(Math.abs(diagnostics.volume_mm3 / (25.4 * 50.8 * 76.2) - 1)).toBeLessThan(1e-6);
  await currentGeometry(page, request);
  await page.locator(".interop-result").scrollIntoViewIfNeeded();
  await page.screenshot({ path: testInfo.outputPath("inch-mesh-diagnostics.png") });
  await page.getByRole("tab", { name: "Exchange", exact: true }).click();
  await page.getByLabel("Export format", { exact: true }).selectOption("obj");
  await page.getByRole("button", { name: "Export document", exact: true }).click();
  const download = page.getByRole("link", { name: "Download OBJ", exact: true });
  await expect(download).toBeVisible({ timeout: 30000 });
  const response = await request.get((await download.getAttribute("href"))!);
  expect(response.ok()).toBeTruthy();
  const obj = await response.text();
  expect(obj).toMatch(/^v /m);
  expect(obj).toMatch(/^f /m);
  expect(obj.length).toBeGreaterThan(100);
  await page.reload();
  await page.getByLabel("Workspace", { exact: true }).selectOption("interop");
  await expect(page.getByLabel("Source asset", { exact: true }).locator("option")).toHaveCount(3);
  expect((await inspection(request)).inspection.bodies[0].bbox).toEqual(bounds);
  await currentGeometry(page, request);
  await page.screenshot({ path: testInfo.outputPath("inch-mesh-reopened.png") });
  expect(errors).toEqual([]);
});

test("construction midplane drives imported sketch, surface trim/thicken, undo and cold reopen", async ({ page, request }, testInfo) => {
  test.setTimeout(120000);
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  page.on("console", message => { if (message.type() === "error") errors.push(message.text()); });
  await blankProject(request, "Browser construction and surface acceptance");
  await page.goto("/");
  await page.getByLabel("Workspace", { exact: true }).selectOption("interop");
  await page.getByRole("tab", { name: "Construction", exact: true }).click();
  await page.getByLabel("Native preparation tool", { exact: true }).selectOption({ label: "Offset construction plane" });
  await applyUi(page, request, "Apply offset construction plane");
  await page.getByLabel("Native preparation tool", { exact: true }).selectOption({ label: "Midplane" });
  await applyUi(page, request, "Apply midplane");
  await page.getByRole("tab", { name: "Exchange", exact: true }).click();
  await page.getByLabel("Sketch import plane", { exact: true }).fill("Midplane");
  await uploadUi(page, request, "Outline.dxf", outlineDxf());
  await page.getByRole("tab", { name: "Surfaces", exact: true }).click();
  await page.getByLabel("sketch", { exact: true }).fill("Outline.dxf");
  await applyUi(page, request, "Apply patch sketch region");
  await page.getByLabel("Native preparation tool", { exact: true }).selectOption({ label: "Trim surface" });
  await page.getByLabel("plane", { exact: true }).fill('{"origin":[3,0,0],"normal":[1,0,0]}');
  await page.getByLabel("keep", { exact: true }).fill("[8,5,5]");
  await applyUi(page, request, "Apply trim surface");
  await page.getByLabel("Native preparation tool", { exact: true }).selectOption({ label: "Thicken surface" });
  await applyUi(page, request, "Apply thicken surface");
  const thickened = await inspection(request);
  const solid = thickened.inspection.bodies.find((body: any) => body.name === "Thickened");
  expect(solid.volume_mm3).toBeCloseTo(280, 6);
  expect(solid.bbox.min[2]).toBeCloseTo(5, 6);
  const timeline = thickened.inspection.timeline;
  expect(timeline.some((feature: any) => feature.name === "Offset plane")).toBe(true);
  expect(timeline.some((feature: any) => feature.name === "Midplane")).toBe(true);
  await currentGeometry(page, request);
  await page.screenshot({ path: testInfo.outputPath("construction-surface-thickened.png") });
  const beforeUndo = (await project(request)).revision;
  await page.getByRole("button", { name: "Project tools", exact: true }).click();
  await page.getByRole("menuitem", { name: "Undo", exact: true }).click();
  await expect.poll(async () => (await project(request)).revision).toBe(beforeUndo + 1);
  expect((await inspection(request)).inspection.bodies.some((body: any) => body.name === "Thickened")).toBe(false);
  await page.getByRole("button", { name: "Project tools", exact: true }).click();
  await page.getByRole("menuitem", { name: "Redo", exact: true }).click();
  await expect.poll(async () => (await project(request)).revision).toBe(beforeUndo + 2);
  await page.reload();
  await page.getByLabel("Workspace", { exact: true }).selectOption("interop");
  const reopened = await inspection(request);
  expect(reopened.document).toEqual(thickened.document);
  expect(reopened.inspection.bodies.find((body: any) => body.name === "Thickened").volume_mm3).toBeCloseTo(280, 6);
  await currentGeometry(page, request);
  await page.getByRole("tab", { name: "Form", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Form editor unavailable", exact: true })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("surface-reopened-and-form-limit.png") });
  expect(errors).toEqual([]);
});
