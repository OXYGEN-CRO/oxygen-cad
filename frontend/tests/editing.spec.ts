import { test, expect, type Page } from "@playwright/test";
import { createHash } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";

let timings: {
  path: string;
  command?: string;
  status: number;
  milliseconds: number;
}[] = [];
test.afterEach(async ({}, info) => {
  writeFileSync(
    info.outputPath("api-timings.json"),
    JSON.stringify(timings, null, 2),
  );
});

async function idle(page: Page) {
  await expect(
    page.getByLabel("Modeling workspace", { exact: true }),
  ).toHaveAttribute("aria-busy", "false", { timeout: 30000 });
}

async function history(page: Page, action: "Undo" | "Redo") {
  await page
    .getByRole("button", { name: "Project tools", exact: true })
    .click();
  await page.getByRole("menuitem", { name: action, exact: true }).click();
}

async function addShape(
  page: Page,
  shape: string,
  values: Record<string, string>,
) {
  await page.getByRole("button", { name: "Add shape", exact: true }).click();
  await page.getByRole("menuitem", { name: shape, exact: true }).click();
  const form = page.getByRole("form", {
    name: `${shape} operation`,
    exact: true,
  });
  await expect(form).toBeVisible();
  for (const [label, value] of Object.entries(values))
    await form.getByLabel(label, { exact: true }).fill(value);
  await form.getByLabel("Operation", { exact: true }).selectOption("new");
  await form
    .getByRole("button", { name: `Apply ${shape.toLowerCase()}`, exact: true })
    .click();
  await idle(page);
  await expect(page.getByText("Geometry current", { exact: true })).toBeVisible(
    { timeout: 30000 },
  );
  await page.getByRole("button", { name: "Fit model", exact: true }).click();
}

test("existing dimensions, basic shapes, feature edits and undo stay editable in the real App", async ({
  page,
  request,
}, info) => {
  test.setTimeout(120000);
  timings = [];
  const started = new Map<any, number>();
  page.on("request", (request) => started.set(request, Date.now()));
  page.on("response", (response) => {
    const request = response.request();
    const path = new URL(response.url()).pathname;
    if (path.startsWith("/api/native/") || path.startsWith("/api/jobs/")) {
      timings.push({
        path,
        command:
          request.method() === "POST" && path.endsWith("/execute")
            ? request.postDataJSON().command
            : undefined,
        status: response.status(),
        milliseconds: Date.now() - (started.get(request) ?? Date.now()),
      });
    }
  });
  const errors: string[] = [];
  const commands: { command: string; params: Record<string, unknown> }[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("request", (request) => {
    if (
      request.method() === "POST" &&
      request.url().endsWith("/api/native/execute")
    )
      commands.push(request.postDataJSON());
  });
  await page.goto("/");
  await expect(
    page.getByText("Geometry current", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Edit dimensions", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Add shape", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Edit dimensions", exact: true })
    .click();
  const width = page.getByLabel("Expression width", { exact: true });
  await expect(width).toBeVisible();
  await width.fill("42 mm");
  await page
    .locator(".modeling-parameter")
    .filter({ has: width })
    .getByRole("button", { name: "Update parameter", exact: true })
    .click();
  await idle(page);
  await expect
    .poll(
      async () =>
        (await (await request.get("/api/project")).json()).parameters.width
          .value,
    )
    .toBe(42);
  await expect(page.getByText("Geometry current", { exact: true })).toBeVisible(
    { timeout: 30000 },
  );
  await page.screenshot({
    path: info.outputPath("01-existing-dimensions.png"),
  });

  await addShape(page, "Box", {
    Length: "12 mm",
    Width: "10 mm",
    Height: "8 mm",
    "Center X": "80 mm",
    "Center Y": "0 mm",
    "Center Z": "4 mm",
    "Feature name": "Added block feature",
    "Body name": "Added block",
  });
  await page
    .locator("#feature-sidebar")
    .getByRole("button", { name: "Added block feature", exact: true })
    .click();
  const height = page.getByLabel("Feature height", { exact: true });
  await expect(height).toBeVisible({ timeout: 30000 });
  await height.fill("12 mm");
  await page
    .getByRole("button", { name: "Save feature definition", exact: true })
    .click();
  await idle(page);
  await expect(height).toHaveValue("12 mm");
  await expect(page.getByText("Geometry current", { exact: true })).toBeVisible(
    { timeout: 30000 },
  );
  await page.screenshot({
    path: info.outputPath("02-edit-existing-feature.png"),
  });

  await history(page, "Undo");
  await expect(height).toHaveValue("8 mm");
  await history(page, "Redo");
  await expect(height).toHaveValue("12 mm");

  await addShape(page, "Cylinder", {
    Diameter: "6 mm",
    Height: "9 mm",
    "Base X": "110 mm",
    "Base Y": "0 mm",
    "Base Z": "0 mm",
    "Feature name": "Added cylinder feature",
    "Body name": "Added cylinder",
  });
  await addShape(page, "Sphere", {
    Radius: "4 mm",
    "Center X": "140 mm",
    "Center Y": "0 mm",
    "Center Z": "4 mm",
    "Feature name": "Added sphere feature",
    "Body name": "Added sphere",
  });
  const saved = await (await request.get("/api/project")).json();
  expect(saved.parameters.width.value).toBe(42);
  expect(saved.parameters.hole_diameter.value).toBe(3.2);
  expect(saved.parameters.hole_diameter.locked).toBeTruthy();
  await page.reload();
  await page
    .locator("#feature-sidebar")
    .getByRole("button", { name: "Added block feature", exact: true })
    .click();
  await expect(page.getByLabel("Feature height", { exact: true })).toHaveValue(
    "12 mm",
    { timeout: 30000 },
  );
  expect(
    (await (await request.get("/api/project")).json()).native_document,
  ).toEqual(saved.native_document);
  await expect(page.getByText("Geometry current", { exact: true })).toBeVisible(
    { timeout: 30000 },
  );
  await page.screenshot({
    path: info.outputPath("03-four-editable-solids-reopened.png"),
  });

  await page.getByLabel("Workspace", { exact: true }).selectOption("interop");
  await page.getByLabel("Export format", { exact: true }).selectOption("step");
  await page
    .getByRole("button", { name: "Export document", exact: true })
    .click();
  const link = page.getByRole("link", { name: "Download STEP", exact: true });
  await expect(link).toBeVisible({ timeout: 30000 });
  const downloaded = page.waitForEvent("download");
  await link.click();
  const step = info.outputPath("edited-parts.step");
  await (await downloaded).saveAs(step);
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
  expect(measured.solids).toBe(4);
  const bodies = measured.bodies.sort(
    (a: any, b: any) => a.center_of_mass_mm[0] - b.center_of_mass_mm[0],
  );
  for (const body of bodies) expect(body.valid).toBeTruthy();
  const bracketVolume =
    60 * 42 * 4 + 4 * 40 * 24 - 4 * 40 * 4 - 6 * Math.PI * 1.6 ** 2 * 4;
  expect(bodies[0].bounds[4] - bodies[0].bounds[1]).toBeCloseTo(42, 4);
  expect(bodies[0].volume_mm3).toBeCloseTo(bracketVolume, 3);
  expect(bodies[1].volume_mm3).toBeCloseTo(12 * 10 * 12, 3);
  for (const [axis, size] of [12, 10, 12].entries())
    expect(bodies[1].bounds[axis + 3] - bodies[1].bounds[axis]).toBeCloseTo(
      size,
      4,
    );
  expect(bodies[1].center_of_mass_mm[0]).toBeCloseTo(80, 4);
  expect(bodies[2].volume_mm3).toBeCloseTo(Math.PI * 3 ** 2 * 9, 3);
  for (const [axis, size] of [6, 6, 9].entries())
    expect(bodies[2].bounds[axis + 3] - bodies[2].bounds[axis]).toBeCloseTo(
      size,
      4,
    );
  expect(bodies[2].center_of_mass_mm[0]).toBeCloseTo(110, 4);
  expect(bodies[2].center_of_mass_mm[2]).toBeCloseTo(4.5, 4);
  expect(bodies[3].volume_mm3).toBeCloseTo((4 * Math.PI * 4 ** 3) / 3, 3);
  for (const axis of [0, 1, 2])
    expect(bodies[3].bounds[axis + 3] - bodies[3].bounds[axis]).toBeCloseTo(
      8,
      4,
    );
  expect(bodies[3].center_of_mass_mm[0]).toBeCloseTo(140, 4);
  expect(bodies[3].center_of_mass_mm[2]).toBeCloseTo(4, 4);
  writeFileSync(
    info.outputPath("independent-edited-step.json"),
    JSON.stringify(measured, null, 2),
  );
  writeFileSync(
    info.outputPath("gui-native-commands.json"),
    JSON.stringify(commands, null, 2),
  );
  expect(
    commands
      .filter((entry) =>
        ["solid.box", "solid.cylinder", "solid.sphere"].includes(entry.command),
      )
      .map((entry) => entry.command),
  ).toEqual(["solid.box", "solid.cylinder", "solid.sphere"]);
  expect(errors).toEqual([]);
});
