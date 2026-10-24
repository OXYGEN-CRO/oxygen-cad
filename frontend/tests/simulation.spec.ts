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
