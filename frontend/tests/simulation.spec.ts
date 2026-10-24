import {
  test,
  expect,
  type Page,
  type APIRequestContext,
} from "@playwright/test";
import { readFileSync } from "node:fs";

let originalProject: any;
test.beforeEach(async ({ request }) => {
  originalProject = await (await request.get("/api/project")).json();
});
test.afterEach(async ({ request }) => {
  if (!originalProject) return;
  const current = await (await request.get("/api/project")).json();
  const response = await request.post("/api/commands", {
    data: {
      expected_revision: current.revision,
      actor: "human",
      project: originalProject,
    },
  });
  expect(response.ok(), await response.text()).toBeTruthy();
});

async function fixture(request: APIRequestContext, thermal = false) {
  const current = await (await request.get("/api/project")).json();
  const p = JSON.parse(readFileSync("../examples/cantilever.json", "utf8"));
  p.revision = current.revision;
  p.requirements = [];
  p.study.mesh_size_mm = 4;
  if (thermal) {
    p.study.type = "thermal";
    p.study.forces = [];
    p.study.fixed_faces = [];
    p.study.stress_probe_mm = null;
    p.study.temperatures = [{ face: "part:xmin", temperature_c: 0 }];
    p.study.heat_sources = [{ face: "part:xmax", power_w: 1 }];
    p.material.thermal_conductivity_w_m_k = 20;
  }
  const r = await request.post("/api/commands", {
    data: { expected_revision: current.revision, actor: "human", project: p },
  });
  expect(r.ok(), await r.text()).toBeTruthy();
}

async function study(page: Page) {
  await page.goto("/");
  await expect(page.locator("canvas")).toBeVisible();
  const build = page.getByRole("button", { name: "Build model", exact: true });
  if (await build.isVisible()) await build.click();
  await expect(page.getByText("Geometry current", { exact: true })).toBeVisible(
    { timeout: 30000 },
  );
  await page
    .getByRole("button", { name: "Project tools", exact: true })
    .click();
  await page
    .getByRole("menuitem", { name: "Study setup", exact: true })
    .click();
}

test("modal setup, actual modes, mode field and restart persistence", async ({
  page,
  request,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await fixture(request);
  await study(page);
  await page.getByLabel("Study type", { exact: true }).selectOption("modal");
  await page.getByLabel("Number of modes", { exact: true }).fill("2");
  await expect(page.getByLabel("Force X", { exact: true })).toHaveCount(0);
  await page.locator(".assumptions input").check();
  await page.getByRole("button", { name: "Save changes", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Run study", exact: true }),
  ).toBeEnabled();
  await page.getByRole("button", { name: "Run study", exact: true }).click();
  await expect(
    page.locator(".result-metric").filter({ hasText: "Mode 1" }),
  ).toBeVisible({ timeout: 45000 });
  const state = await (await request.get("/api/state")).json();
  expect(state.analysis.frequencies_hz[0]).toBeGreaterThan(490);
  expect(state.analysis.frequencies_hz[0]).toBeLessThan(530);
  expect(state.analysis.physical_validation).toBe("not_performed");
  await expect(
    page.getByRole("button", { name: "Run study", exact: true }),
  ).toBeEnabled();
  await page.getByRole("button", { name: "View mode", exact: true }).click();
  await page.getByLabel("Mode number").selectOption("1");
  await page.getByLabel("Deformation scale").press("ArrowRight");
  await expect(page.locator(".webgl")).toHaveAttribute(
    "data-field-ready",
    "true",
  );
  await expect(
    page.getByText("Mode shape · relative amplitude", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Fit model", exact: true }).click();
  await page.screenshot({
    path: "test-results/study-modal.png",
    fullPage: true,
  });
  await page.reload();
  await page
    .getByRole("button", { name: "Project tools", exact: true })
    .click();
  await page
    .getByRole("menuitem", { name: "Study setup", exact: true })
    .click();
  await expect(page.getByLabel("Study type", { exact: true })).toHaveValue(
    "modal",
  );
  expect(errors).toEqual([]);
});

test("thermal solve, temperature field, energy balance and stale edit", async ({
  page,
  request,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await fixture(request, true);
  await study(page);
  await expect(
    page.getByLabel("Thermal conductivity", { exact: true }),
  ).toHaveValue("20");
  await page.getByRole("button", { name: "Run study", exact: true }).click();
  await expect(
    page.getByText("Maximum temperature", { exact: true }),
  ).toBeVisible({ timeout: 45000 });
  const state = await (await request.get("/api/state")).json();
  expect(state.analysis.max_temperature_c).toBeCloseTo(100, 0);
  expect(state.analysis.heat_balance_error).toBeLessThan(0.01);
  await expect(
    page.getByRole("button", { name: "Run study", exact: true }),
  ).toBeEnabled();
  await page
    .getByRole("button", { name: "View temperature", exact: true })
    .click();
  await expect(page.locator(".webgl")).toHaveAttribute(
    "data-field-ready",
    "true",
  );
  await expect(
    page.getByText("Temperature · °C", { exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: "test-results/study-thermal.png",
    fullPage: true,
  });
  await page.getByLabel("Heat power 1", { exact: true }).fill("2");
  await page.getByRole("button", { name: "Save changes", exact: true }).click();
  await expect
    .poll(
      async () =>
        (await (await request.get("/api/state")).json()).analysis_state,
    )
    .toBe("stale");
  await expect(
    page.getByText("Maximum temperature", { exact: true }),
  ).toHaveCount(0);
  expect(errors).toEqual([]);
});

test("named studies preserve independent loads, results and comparison", async ({
  page,
  request,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await fixture(request);
  await study(page);
  await page
    .getByRole("button", { name: "Run static study", exact: true })
    .click();
  await expect(
    page.getByText("Maximum displacement", { exact: true }),
  ).toBeVisible({ timeout: 45000 });
  await expect(
    page.getByRole("button", { name: "Run static study", exact: true }),
  ).toBeEnabled();
  const first = (await (await request.get("/api/state")).json()).analysis
    .max_displacement_mm;
  await page
    .getByRole("button", { name: "Duplicate study", exact: true })
    .click();
  await expect(
    page.getByText("Maximum displacement", { exact: true }),
  ).toHaveCount(0);
  await page.getByLabel("Study name", { exact: true }).fill("Double load");
  await page.getByLabel("Force Z", { exact: true }).fill("-20");
  await page.locator(".assumptions input").check();
  await page.getByRole("button", { name: "Save changes", exact: true }).click();
  await expect(page.getByLabel("Saved study", { exact: true })).toHaveValue(
    "study_1",
  );
  await page
    .getByRole("button", { name: "Run static study", exact: true })
    .click();
  await expect(
    page.getByText("Maximum displacement", { exact: true }),
  ).toBeVisible({ timeout: 45000 });
  await expect(
    page.getByRole("button", { name: "Run static study", exact: true }),
  ).toBeEnabled();
  let state = await (await request.get("/api/state")).json();
  expect(state.analysis.max_displacement_mm / first).toBeCloseTo(2, 2);
  await page.getByText("Compare studies", { exact: true }).click();
  await expect(
    page.getByRole("table", { name: "Study comparison", exact: true }),
  ).toContainText("Double load");
  await page.getByLabel("Saved study", { exact: true }).selectOption("default");
  await expect(
    page.getByText("Maximum displacement", { exact: true }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "Save changes", exact: true }).click();
  await expect(page.getByLabel("Force Z", { exact: true })).toHaveValue("-10");
  await expect(
    page.getByText("Maximum displacement", { exact: true }),
  ).toBeVisible();
  state = await (await request.get("/api/state")).json();
  expect(state.analysis.max_displacement_mm).toBeCloseTo(first, 5);
  expect(state.studies.study_1.result.max_displacement_mm / first).toBeCloseTo(
    2,
    2,
  );
  await page.reload();
  await page
    .getByRole("button", { name: "Project tools", exact: true })
    .click();
  await page
    .getByRole("menuitem", { name: "Study setup", exact: true })
    .click();
  await expect(page.getByLabel("Saved study", { exact: true })).toHaveValue(
    "default",
  );
  await expect(page.getByLabel("Saved study", { exact: true })).toContainText(
    "Double load",
  );
  expect(errors).toEqual([]);
});

test("plastic nonlinear setup, load/unload history and increment fields", async ({
  page,
  request,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await fixture(request);
  await study(page);
  await page
    .getByLabel("Study type", { exact: true })
    .selectOption("nonlinear_static");
  await page.getByLabel("Poisson ratio", { exact: true }).fill("0");
  await page.getByLabel("Force X", { exact: true }).fill("10800");
  await page.getByLabel("Force Z", { exact: true }).fill("0");
  await page.getByLabel("Large deformation", { exact: true }).uncheck();
  await page.getByLabel("Load increments", { exact: true }).fill("4");
  await page
    .getByRole("button", { name: "Add hardening point", exact: true })
    .click();
  await page.getByLabel("Yield stress 1", { exact: true }).fill("250");
  await page
    .getByRole("button", { name: "Add hardening point", exact: true })
    .click();
  await page.getByLabel("Yield stress 2", { exact: true }).fill("350");
  await page.getByLabel("Plastic strain 2", { exact: true }).fill("0.01");
  await page
    .getByLabel("Plastic curve source", { exact: true })
    .fill(
      "Synthetic analytical fixture: true stress/equivalent plastic strain, not production data",
    );
  await page
    .getByRole("button", { name: "Add load / unload path", exact: true })
    .click();
  await page.locator(".assumptions input").check();
  await page.getByRole("button", { name: "Save changes", exact: true }).click();
  await page.getByRole("button", { name: "Run study", exact: true }).click();
  await expect(
    page.getByText("Final displacement", { exact: true }),
  ).toBeVisible({ timeout: 45000 });
  await expect(
    page.getByRole("button", { name: "Run study", exact: true }),
  ).toBeEnabled();
  const state = await (await request.get("/api/state")).json();
  expect(state.analysis.completed_load_path).toBe(true);
  expect(state.analysis.max_displacement_mm).toBeGreaterThan(0.25);
  expect(state.analysis.final_max_displacement_mm).toBeLessThan(0.17);
  await expect(
    page
      .getByRole("table", { name: "Load history", exact: true })
      .locator("tbody tr"),
  ).toHaveCount(4);
  await page
    .getByRole("button", { name: "View plastic strain", exact: true })
    .click();
  await page.getByLabel("Load increment", { exact: true }).selectOption("1");
  await expect(page.locator(".webgl")).toHaveAttribute(
    "data-field-artifact",
    state.analysis.increments[1].field_artifact,
  );
  await expect(page.locator(".webgl")).toHaveAttribute(
    "data-field-ready",
    "true",
  );
  await expect(
    page.getByText("Equivalent plastic strain · dimensionless", {
      exact: true,
    }),
  ).toBeVisible();
  await page
    .getByLabel("Deformation scale", { exact: true })
    .press("ArrowRight");
  await page.getByRole("button", { name: "Fit model", exact: true }).click();
  await expect(page.locator(".webgl")).toHaveAttribute(
    "data-field-ready",
    "true",
  );
  await page.screenshot({
    path: "test-results/study-nonlinear.png",
    fullPage: true,
  });
  await page.getByLabel("Load increment", { exact: true }).selectOption("3");
  await expect(page.locator(".webgl")).toHaveAttribute(
    "data-field-artifact",
    state.analysis.increments[3].field_artifact,
  );
  await expect(page.locator(".webgl")).toHaveAttribute(
    "data-field-ready",
    "true",
  );
  expect(errors).toEqual([]);
});
