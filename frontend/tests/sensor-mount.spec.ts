import { test, expect } from "@playwright/test";
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const expectedName = process.env.O3D_ACCEPTANCE_NAME;
const output = process.env.O3D_ACCEPTANCE_OUTPUT;

test("sensor carrier: real UI edit invalidates geometry and analysis, rebuild preserves locks", async ({
  page,
  request,
}) => {
  test.skip(
    !expectedName || !output,
    "Run only from the isolated sensor-mount acceptance driver",
  );
  const before = await (await request.get("/api/project")).json();
  expect(before.name).toBe(expectedName);
  expect(before.references).toHaveLength(1);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/");
  await expect(
    page.getByText("Geometry current", { exact: true }),
  ).toBeVisible();
  await expect(page.locator("canvas")).toBeVisible();
  mkdirSync(output!, { recursive: true });
  await page.screenshot({
    path: join(output!, "studio-initial.png"),
    fullPage: true,
  });
  const collapse = page.getByRole("button", {
    name: "Collapse inspector sidebar",
    exact: true,
  });
  if (await collapse.isVisible()) await collapse.click();
  await page
    .getByRole("button", { name: "Mounting plate", exact: false })
    .click();
  await expect(page.getByLabel("Width", { exact: true })).toHaveValue(
    "$length",
  );
  await page.getByLabel("Width", { exact: true }).fill("46");
  await page.getByRole("button", { name: "Save changes", exact: true }).click();
  await expect
    .poll(
      async () =>
        (await (await request.get("/api/project")).json()).features[0].width,
    )
    .toBe(46);
  await expect(
    page.getByText("Showing previous geometry · rebuild required"),
  ).toBeVisible();
  const stale = await (await request.get("/api/state")).json();
  expect(stale.geometry_state).toBe("stale");
  expect(stale.analysis_state).toBe("stale");
  await page.screenshot({
    path: join(output!, "studio-stale.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "Build model", exact: true }).click();
  await expect(page.getByText("Geometry current", { exact: true })).toBeVisible(
    { timeout: 30000 },
  );
  const close = page.getByRole("button", {
    name: "Close properties",
    exact: true,
  });
  if (await close.isVisible()) await close.click();
  const canvas = page.locator("canvas");
  const bounds = await canvas.boundingBox();
  if (!bounds) throw new Error("Missing model canvas");
  for (const [dx, dy] of [
    [0, 0],
    [20, 20],
    [-40, 20],
    [40, -20],
    [-80, 0],
    [0, 60],
  ]) {
    await page.mouse.click(
      bounds.x + bounds.width / 2 + dx,
      bounds.y + bounds.height / 2 + dy,
    );
    if (
      (await (await request.get("/api/state")).json()).context.selection?.face
    )
      break;
  }
  const after = await (await request.get("/api/state")).json();
  expect(after.context.selection?.face).toBeTruthy();
  expect(after.project.parameters.sensor_hole_diameter).toEqual(
    before.parameters.sensor_hole_diameter,
  );
  expect(after.build.dimensions_mm[0]).toBeCloseTo(46, 4);
  expect(after.project.revision).toBe(before.revision + 1);
  expect(errors).toEqual([]);
  await page.screenshot({
    path: join(output!, "studio-edited.png"),
    fullPage: true,
  });
  writeFileSync(
    join(output!, "browser-evidence.json"),
    JSON.stringify(
      {
        before_revision: before.revision,
        after_revision: after.project.revision,
        stale_geometry: stale.geometry_state,
        stale_analysis: stale.analysis_state,
        dimensions_mm: after.build.dimensions_mm,
        actual_selection: after.context.selection,
        runtime_errors: errors,
      },
      null,
      2,
    ),
  );
});
