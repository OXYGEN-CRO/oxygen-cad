import { expect, test } from "@playwright/test";

test("simulation setup persists sourced values and case boundaries through panel callbacks", async ({
  page,
}) => {
  await page.goto("/src/workspaces/simulation/__fixtures__/panel.html");
  await page.getByRole("button", { name: "New study", exact: true }).click();
  await page
    .getByLabel("Study name", { exact: true })
    .fill("Explicit axial test");
  await expect(page.getByLabel("Element family", { exact: true })).toHaveValue(
    "C3D10",
  );
  await page
    .getByLabel("Displacement probe (mm)", { exact: true })
    .fill("10, 1, 1");
  await page.getByRole("button", { name: "Set probe", exact: true }).click();
  await page.getByRole("button", { name: "Add support", exact: true }).click();
  await page.getByRole("button", { name: "Add load", exact: true }).click();
  await page.getByLabel("Load 1 X", { exact: true }).fill("100");
  await page
    .getByLabel("Load source", { exact: true })
    .fill("Declared analytical test force");
  await page.getByRole("checkbox", { name: /I reviewed/ }).check();
  await expect(
    page.getByRole("button", { name: "Generate mesh", exact: true }),
  ).toBeDisabled();
  await page.getByRole("button", { name: "Save setup", exact: true }).click();
  await expect(page.getByLabel("Saved revision", { exact: true })).toHaveText(
    "2",
  );
  const saved = JSON.parse(
    await page
      .getByLabel("Saved simulation setup", { exact: true })
      .innerText(),
  );
  expect(saved.studies[0].name).toBe("Explicit axial test");
  expect(saved.studies[0].settings.probe_point_mm).toEqual([10, 1, 1]);
  expect(saved.studies[0].settings.probe_body_id).toBe("part");
  expect(saved.studies[0].cases[0].loads[0].vector).toEqual([100, 0, 0]);
  expect(saved.studies[0].cases[0].loads[0].source).toBe(
    "Declared analytical test force",
  );
  expect(saved.materials[0].source).toBe(
    "Synthetic form fixture, no material calibration",
  );
  expect(saved.models[0].regions[0].selection).toBe("part:xmax");
  await expect(
    page.getByRole("button", { name: "Generate mesh", exact: true }),
  ).toBeEnabled();
  await expect(
    page.getByRole("button", { name: "Solve all cases", exact: true }),
  ).toBeDisabled();
  await expect(page.getByRole("alert")).toBeEmpty();
});

test("actual analytical FE field renders in the domain WebGL preview", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/src/workspaces/simulation/__fixtures__/panel.html");
  await page
    .getByText("Analytical reference numerical field", { exact: true })
    .click();
  const canvas = page
    .getByLabel("Numerical finite element field preview", { exact: true })
    .locator("canvas");
  await expect(canvas).toBeVisible();
  const bounds = await canvas.boundingBox();
  expect(bounds?.width).toBeGreaterThan(200);
  expect(bounds?.height).toBe(230);
  expect(errors).toEqual([]);
});

test("changing study family keeps an incompatible saved probe available for explicit clearing", async ({
  page,
}) => {
  await page.goto("/src/workspaces/simulation/__fixtures__/panel.html");
  await page.getByRole("button", { name: "New study", exact: true }).click();
  await page
    .getByLabel("Displacement probe (mm)", { exact: true })
    .fill("10, 1, 1");
  await page.getByRole("button", { name: "Set probe", exact: true }).click();
  await page
    .getByRole("combobox", { name: "Study type", exact: true })
    .selectOption("modal");
  await expect(
    page.getByRole("button", { name: "Set probe", exact: true }),
  ).toBeDisabled();
  await expect(
    page.getByRole("button", { name: "Clear probe", exact: true }),
  ).toBeEnabled();
  await page.getByRole("button", { name: "Clear probe", exact: true }).click();
  await page.getByRole("button", { name: "Save setup", exact: true }).click();
  const saved = JSON.parse(
    await page
      .getByLabel("Saved simulation setup", { exact: true })
      .innerText(),
  );
  expect(saved.studies[0].type).toBe("modal");
  expect(saved.studies[0].settings.probe_point_mm).toBeNull();
  expect(saved.studies[0].settings.probe_body_id).toBeNull();
});
