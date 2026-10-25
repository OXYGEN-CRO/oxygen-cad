import { expect, test, type APIRequestContext } from "@playwright/test";

async function complete(request: APIRequestContext, id: string) {
  let job: any;
  await expect
    .poll(
      async () => {
        job = await (await request.get(`/api/jobs/${id}`)).json();
        return job.state;
      },
      { timeout: 540000 },
    )
    .not.toMatch(/queued|running/);
  expect(job.state, job.message).toBe("succeeded");
  return job.result;
}

test("native plastic history reports actual increment coverage and preserves physical limits", async ({
  page,
  request,
}) => {
  test.setTimeout(660000);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const original = await (await request.get("/api/project")).json();
  try {
    const reset = await request.post("/api/commands", {
      data: {
        expected_revision: original.revision,
        actor: "human",
        project: {
          ...original,
          name: "Owned plastic history browser reference",
          geometry_engine: "solidcraft",
          native_document: null,
          features: [],
          parameters: {},
          references: [],
          domains: {},
          material: null,
          requirements: [],
          placement: { translation: [0, 0, 0], rotation: [0, 0, 0] },
        },
      },
    });
    expect(reset.ok(), await reset.text()).toBeTruthy();
    const command = await request.post("/api/native/execute", {
      data: {
        expected_revision: (await reset.json()).project.revision,
        actor: "human",
        command: "solid.box",
        params: {
          length: 4,
          width: 1,
          height: 1,
          body_name: "Numerical plastic bar",
        },
      },
    });
    expect(command.ok(), await command.text()).toBeTruthy();
    const built = await complete(
      request,
      (await (await request.post("/api/jobs/build")).json()).id,
    );
    const body = built.scene.bodies[0];
    expect(body.bbox).toEqual({ min: [0, 0, 0], max: [4, 1, 1] });
    const state = await (await request.get("/api/state")).json();
    const project = await (await request.get("/api/project")).json();
    const regions = [
      ["root", 0, -1],
      ["tip", 0, 1],
      ["y", 1, -1],
      ["z", 2, -1],
    ].map(([id, axis, sign]) => ({
      id,
      body_id: body.id,
      selection: body.faces.find(
        (f: any) => f.normal && Number(sign) * f.normal[Number(axis)] > 0.99,
      ).id,
    }));
    const saved = await request.put("/api/domains/simulation", {
      data: {
        expected_revision: project.revision,
        actor: "human",
        data: {
          schema_version: 1,
          active_study_id: "plastic",
          models: [
            {
              id: "model",
              name: "Native bar",
              geometry_key: state.geometry_key,
              body_ids: [body.id],
              regions,
            },
          ],
          materials: [
            {
              id: "j2",
              name: "Synthetic J2 reference",
              source: "Declared numerical reference; no physical calibration",
              youngs_modulus_mpa: 200000,
              poisson_ratio: 0,
              plastic_curve: [
                [250, 0],
                [350, 0.01],
              ],
              plastic_source:
                "Declared true stress and equivalent plastic strain",
            },
          ],
          studies: [
            {
              id: "plastic",
              name: "Material history reference",
              type: "nonlinear_static",
              model_id: "model",
              assignments: [{ body_id: body.id, material_id: "j2" }],
              assumptions_confirmed: true,
              settings: { probe_point_mm: [4, 0, 0], probe_body_id: body.id },
              cases: [
                {
                  id: "axial",
                  name: "Plastic history reference",
                  history: [
                    [0, 0],
                    [1, 1],
                    [2, 0],
                  ],
                  supports: [
                    {
                      id: "root",
                      region_id: "root",
                      kind: "partial",
                      components: [true, false, false],
                    },
                    {
                      id: "y",
                      region_id: "y",
                      kind: "partial",
                      components: [false, true, false],
                    },
                    {
                      id: "z",
                      region_id: "z",
                      kind: "partial",
                      components: [false, false, true],
                    },
                  ],
                  loads: [
                    {
                      id: "pull",
                      region_id: "tip",
                      vector: [300, 0, 0],
                      source: "Declared 300 N on one square mm",
                    },
                  ],
                },
              ],
            },
          ],
        },
      },
    });
    expect(saved.ok(), await saved.text()).toBeTruthy();
    await page.goto("/");
    await page
      .getByLabel("Workspace", { exact: true })
      .selectOption("simulation");
    await page.getByLabel("Initial size (mm)", { exact: true }).fill("1.0");
    await page.getByLabel("Mesh levels", { exact: true }).fill("2");
    await page
      .getByRole("button", { name: "Generate mesh", exact: true })
      .click();
    await expect
      .poll(
        async () =>
          (await (await request.get("/api/project")).json()).domains.simulation
            .models[0].mesh?.element_family,
        { timeout: 120000 },
      )
      .toBe("C3D10");
    await expect(
      page.getByRole("button", { name: "Solve all cases", exact: true }),
    ).toBeEnabled();
    const solve = page.waitForResponse(
      (r) =>
        r.url().endsWith("/api/domains/simulation/run") &&
        r.request().method() === "POST",
    );
    await page
      .getByRole("button", { name: "Solve all cases", exact: true })
      .click();
    const response = await solve;
    expect(response.ok(), await response.text()).toBeTruthy();
    const job = await response.json();
    const answer = await complete(request, job.id);
    const result = answer.results[0];
    expect(result.values.probe_displacement_mm).toBeCloseTo(0.026, 6);
    expect(result.displacement_probe.final.magnitude_mm).toBeCloseTo(0.02, 6);
    const history = result.checks.material_derivative_history;
    expect(history.accepted_frames).toBeGreaterThan(1);
    expect(history.qualified_plastic_frames).toBeGreaterThan(0);
    expect(history.full_contact_stability_qualified).toBe(false);
    expect(history.accepted_state_tangent_qualified).toBe(false);
    expect(result.physical_validation).toBe("not_supplied");
    await expect(
      page.getByText(
        new RegExp(
          `Material history: ${history.qualified_plastic_frames}/${history.plastic_frames} accepted plastic increments`,
        ),
      ),
    ).toBeVisible({ timeout: 30000 });
    await expect(
      page.getByText(
        /Changing contact maps, stability and physical materials require further qualification/,
      ),
    ).toBeVisible();
    const exporting = page.waitForResponse(
      (r) =>
        r.url().endsWith("/api/domains/simulation/run") &&
        r.request().method() === "POST",
    );
    await page
      .getByRole("button", { name: "Export analysis package", exact: true })
      .click();
    const submitted = await exporting;
    expect(submitted.ok(), await submitted.text()).toBeTruthy();
    const exported = await complete(request, (await submitted.json()).id);
    expect(exported.artifact).toMatch(/analysis-package\.zip$/);
    await test.info().attach("material-history-evidence", {
      body: JSON.stringify(
        {
          native_bounds: body.bbox,
          checks: result.checks,
          values: result.values,
          physical_validation: result.physical_validation,
          export: exported,
        },
        null,
        2,
      ),
      contentType: "application/json",
    });
    expect(errors).toEqual([]);
    await page.screenshot({
      path: "test-results/material-history.png",
      fullPage: true,
    });
  } finally {
    const current = await (await request.get("/api/project")).json();
    const restored = await request.post("/api/commands", {
      data: {
        expected_revision: current.revision,
        actor: "human",
        action: "restore",
        restore_revision: original.revision,
      },
    });
    expect(restored.ok(), await restored.text()).toBeTruthy();
  }
});
