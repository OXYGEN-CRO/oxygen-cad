import { expect, test, type APIRequestContext } from "@playwright/test";
async function complete(request: APIRequestContext, id: string) {
  let job: any;
  await expect
    .poll(
      async () => {
        job = await (await request.get(`/api/jobs/${id}`)).json();
        return job.state;
      },
      { timeout: 180000 },
    )
    .not.toMatch(/queued|running/);
  expect(job.state, job.message).toBe("succeeded");
  return job.result;
}
test("native fluid volume solves and displays real flow while retaining the pressure accuracy hold", async ({
  page,
  request,
}) => {
  test.skip(
    !process.env.O3D_OPENFOAM_ROOT,
    "Pinned isolated OpenFOAM runtime is required",
  );
  test.setTimeout(240000);
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const original = await (await request.get("/api/project")).json();
  try {
    const reset = await request.post("/api/commands", {
      data: {
        expected_revision: original.revision,
        actor: "human",
        project: {
          ...original,
          name: "Owned native fluid reference",
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
          length: 10,
          width: 10,
          height: 10,
          body_name: "Declared fluid volume",
        },
      },
    });
    expect(command.ok(), await command.text()).toBeTruthy();
    const built = await complete(
      request,
      (await (await request.post("/api/jobs/build")).json()).id,
    );
    const body = built.scene.bodies[0];
    expect(body.bbox).toEqual({ min: [0, 0, 0], max: [10, 10, 10] });
    const regions: any[] = [];
    const boundaries: any[] = [];
    for (let axis = 0; axis < 3; axis++)
      for (const sign of [-1, 1]) {
        const id = "xyz"[axis] + (sign < 0 ? "min" : "max");
        const face = body.faces.find(
          (f: any) => f.normal && sign * f.normal[axis] > 0.99,
        );
        regions.push({ id, body_id: body.id, selection: face.id });
        const kind =
          axis === 0
            ? "pressure"
            : axis === 2
              ? "symmetry"
              : sign > 0
                ? "moving_wall"
                : "no_slip";
        boundaries.push({
          id,
          region_id: id,
          kind,
          source: "Declared synthetic Couette boundary",
          velocity_m_s: kind === "moving_wall" ? [0.1, 0, 0] : null,
          pressure_gauge_pa: kind === "pressure" ? 0 : null,
        });
      }
    const state = await (await request.get("/api/state")).json();
    const project = await (await request.get("/api/project")).json();
    const saved = await request.put("/api/domains/simulation", {
      data: {
        expected_revision: project.revision,
        actor: "human",
        data: {
          schema_version: 1,
          active_study_id: "flow",
          models: [
            {
              id: "model",
              name: "Native fluid volume",
              geometry_key: state.geometry_key,
              body_ids: [body.id],
              regions,
            },
          ],
          materials: [
            {
              id: "fluid",
              name: "Synthetic fluid",
              source: "Numerical reference only",
              density_g_cm3: 0.001,
              dynamic_viscosity_pa_s: 0.001,
              property_sources: {
                density_g_cm3: "Declared 1 kg/m3",
                dynamic_viscosity_pa_s: "Declared 0.001 Pa s",
              },
            },
          ],
          studies: [
            {
              id: "flow",
              name: "Native flow accuracy hold",
              type: "general_cfd",
              model_id: "model",
              assignments: [{ body_id: body.id, material_id: "fluid" }],
              cases: [{ id: "couette", name: "Moving wall", flow: boundaries }],
              flow: {
                domain_kind: "fluid_volume",
                model: "steady_laminar_incompressible_newtonian",
                source: "Explicit fluid volume; numerical model only",
                maximum_iterations: 2000,
              },
              settings: { residual_tolerance: 1e-6 },
              assumptions_confirmed: true,
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
    await expect(
      page.getByRole("combobox", { name: "Study type", exact: true }),
    ).toHaveValue("general_cfd");
    await expect(
      page.getByLabel("Element family", { exact: true }),
    ).toHaveValue("C3D4");
    await page
      .getByLabel("Fluid model and domain source", { exact: true })
      .fill(
        "Explicit native fluid volume reviewed in browser; no physical calibration",
      );
    await page.getByRole("button", { name: "Save setup", exact: true }).click();
    await page.getByLabel("Initial size (mm)", { exact: true }).fill("4");
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
      .toBe("C3D4");
    await expect(
      page.getByRole("button", { name: "Solve all cases", exact: true }),
    ).toBeEnabled();
    const solving = page.waitForResponse(
      (r) =>
        r.url().endsWith("/api/domains/simulation/run") &&
        r.request().method() === "POST",
    );
    await page
      .getByRole("button", { name: "Solve all cases", exact: true })
      .click();
    const submitted = await solving;
    expect(submitted.ok(), await submitted.text()).toBeTruthy();
    const result = (await complete(request, (await submitted.json()).id))
      .results[0];
    expect(result.numerical_state).toBe("inconclusive");
    expect(result.physical_validation).toBe("not_supplied");
    expect(result.checks.pressure_field_spatial_accuracy_qualified).toBe(false);
    expect(result.values.max_speed_m_s).toBeGreaterThan(0.08);
    expect(result.values.max_speed_m_s).toBeLessThan(0.11);
    await expect(
      page.getByText(
        /pressure-field spatial accuracy on barycentric control volumes is not qualified/,
      ),
    ).toBeVisible();
    await page
      .getByRole("button", { name: "View numerical field", exact: true })
      .click();
    await expect(
      page.getByLabel("Field quantity", { exact: true }),
    ).toHaveValue("velocity");
    await expect(
      page
        .getByLabel("Numerical finite element field preview", { exact: true })
        .locator("canvas"),
    ).toBeVisible();
    await page
      .getByLabel("Field quantity", { exact: true })
      .selectOption("flow_pressure");
    await expect(
      page.getByLabel("Field color range", { exact: true }),
    ).toContainText("Pa gauge");
    await expect(
      page.getByLabel("Deformation display scale ×", { exact: true }),
    ).toHaveCount(0);
    const exporting = page.waitForResponse(
      (r) =>
        r.url().endsWith("/api/domains/simulation/run") &&
        r.request().method() === "POST",
    );
    await page
      .getByRole("button", { name: "Export analysis package", exact: true })
      .click();
    const response = await exporting;
    expect(response.ok(), await response.text()).toBeTruthy();
    const exported = await complete(request, (await response.json()).id);
    expect(exported.manifest.transfer_losses.join(" ")).toContain(
      "pressure-field accuracy remains unqualified",
    );
    await test.info().attach("native-flow-evidence", {
      body: JSON.stringify(
        {
          values: result.values,
          checks: result.checks,
          mesh_iterations: result.mesh_iterations,
          verification_gaps: result.verification_gaps,
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
      path: "test-results/native-flow.png",
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
