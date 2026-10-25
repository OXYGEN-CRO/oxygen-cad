import { expect, test, type APIRequestContext } from "@playwright/test";
async function complete(request: APIRequestContext, id: string) {
  let job: any;
  await expect
    .poll(
      async () => {
        job = await (await request.get(`/api/jobs/${id}`)).json();
        return job.state;
      },
      { timeout: 120000 },
    )
    .not.toMatch(/queued|running/);
  expect(job.state, job.message).toBe("succeeded");
  return job.result;
}

test("native solids transfer loads through a sourced surface bond edited in the panel", async ({
  page,
  request,
}) => {
  test.setTimeout(180000);
  const original = await (await request.get("/api/project")).json();
  try {
    const reset = await request.post("/api/commands", {
      data: {
        expected_revision: original.revision,
        actor: "human",
        project: {
          ...original,
          name: "Disposable bonded assembly reference",
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
    for (const [name, corner] of [
      ["First", [0, 0, 0]],
      ["Second", [10, 0, 0]],
    ] as const) {
      const project = await (await request.get("/api/project")).json();
      const response = await request.post("/api/native/execute", {
        data: {
          expected_revision: project.revision,
          actor: "human",
          command: "solid.box",
          params: { length: 10, width: 2, height: 2, corner, body_name: name },
        },
      });
      expect(response.ok(), await response.text()).toBeTruthy();
    }
    await complete(
      request,
      (await (await request.post("/api/jobs/build")).json()).id,
    );
    const state = await (await request.get("/api/state")).json();
    const bodies = state.build.scene.bodies;
    const first = bodies.find((b: any) => b.name === "First"),
      second = bodies.find((b: any) => b.name === "Second");
    const face = (body: any, sign: number) =>
      body.faces.find((f: any) => f.normal && sign * f.normal[0] > 0.99);
    const regions = [
      { id: "root", body_id: first.id, selection: face(first, -1).id },
      { id: "primary", body_id: first.id, selection: face(first, 1).id },
      { id: "secondary", body_id: second.id, selection: face(second, -1).id },
      { id: "tip", body_id: second.id, selection: face(second, 1).id },
    ];
    const project = await (await request.get("/api/project")).json();
    const data = {
      schema_version: 1,
      active_study_id: "assembly",
      models: [
        {
          id: "model",
          name: "Native bonded pair",
          geometry_key: state.geometry_key,
          body_ids: [first.id, second.id],
          regions,
        },
      ],
      materials: [
        {
          id: "synthetic",
          name: "Declared numerical material",
          source: "SI browser reference, no process calibration",
          youngs_modulus_mpa: 200000,
          poisson_ratio: 0,
        },
      ],
      studies: [
        {
          id: "assembly",
          name: "Actual surface bond",
          type: "linear_static",
          model_id: "model",
          assignments: [first, second].map((body) => ({
            body_id: body.id,
            material_id: "synthetic",
          })),
          cases: [
            {
              id: "pull",
              name: "Bonded SI reference",
              supports: [{ id: "root", region_id: "root" }],
              loads: [
                {
                  id: "force",
                  region_id: "tip",
                  vector: [100, 0, 0],
                  source: "Declared100Ntest",
                },
              ],
            },
          ],
          interfaces: [],
          settings: { probe_point_mm: [20, 1, 1], probe_body_id: second.id },
          assumptions_confirmed: true,
        },
      ],
    };
    const save = await request.put("/api/domains/simulation", {
      data: { expected_revision: project.revision, actor: "human", data },
    });
    expect(save.ok(), await save.text()).toBeTruthy();
    await page.goto("/");
    await page
      .getByLabel("Workspace", { exact: true })
      .selectOption("simulation");
    await page.getByText("Bonded interfaces", { exact: true }).click();
    await page
      .getByRole("button", { name: "Add bonded interface", exact: true })
      .click();
    await page
      .getByLabel("Bond 1 primary region", { exact: true })
      .selectOption("primary");
    await page
      .getByLabel("Bond 1 secondary region", { exact: true })
      .selectOption("secondary");
    await page
      .getByLabel("Bond 1 mapping tolerance (mm)", { exact: true })
      .fill("0.0000001");
    await page
      .getByLabel("Bond 1 source", { exact: true })
      .fill("Declared ideal zero-thickness numerical bond");
    await page.getByRole("button", { name: "Save setup", exact: true }).click();
    await expect
      .poll(
        async () =>
          (await (await request.get("/api/project")).json()).domains.simulation
            .studies[0].interfaces[0]?.source,
      )
      .toBe("Declared ideal zero-thickness numerical bond");
    await expect(
      page.getByRole("status").filter({ hasText: /^Saving/ }),
    ).toHaveCount(0);
    const saved = await (await request.get("/api/project")).json();
    expect(saved.domains.simulation.studies[0].interfaces[0].formulation).toBe(
      "quadratic_surface_mortar",
    );
    expect(saved.domains.simulation.materials[0].density_g_cm3).toBeNull();
    await page.getByLabel("Initial size (mm)", { exact: true }).fill("1.5");
    await page.getByLabel("Mesh levels", { exact: true }).fill("2");
    await page
      .getByRole("button", { name: "Generate mesh", exact: true })
      .click();
    await expect
      .poll(
        async () =>
          (await (await request.get("/api/project")).json()).domains.simulation
            .models[0].refinements?.length,
        { timeout: 120000 },
      )
      .toBe(1);
    await expect(
      page.getByRole("button", { name: "Solve all cases", exact: true }),
    ).toBeEnabled();
    await page
      .getByRole("button", { name: "Solve all cases", exact: true })
      .click();
    await expect(
      page.getByText("Bonded SI reference · verified numerically", {
        exact: true,
      }),
    ).toBeVisible({ timeout: 120000 });
    const link = page.getByRole("link", {
      name: "Bond interface fields",
      exact: true,
    });
    const response = await request.get((await link.getAttribute("href"))!);
    expect(response.ok()).toBeTruthy();
    const evidence = await response.json();
    expect(evidence.interfaces).toHaveLength(1);
    expect(evidence.interfaces[0].secondary_resultant_n[0]).toBeCloseTo(
      -100,
      6,
    );
    expect(
      evidence.interfaces[0].action_reaction_moment_error_n_mm,
    ).toBeLessThan(1e-7);
    expect(
      evidence.interfaces[0].mortar.operator_quadrature_sensitivity,
    ).toBeLessThan(1e-10);
    await page
      .getByRole("button", { name: "View numerical field", exact: true })
      .click();
    const preview = page.getByLabel("Numerical finite element field preview", {
      exact: true,
    });
    await preview.scrollIntoViewIfNeeded();
    await expect(preview.locator("canvas")).toBeVisible();
    await page
      .getByLabel("Field quantity", { exact: true })
      .selectOption("stress");
    await expect(
      page.getByLabel("Field color range", { exact: true }),
    ).toHaveText("Uniform at displayed precision: 25.0000 MPa");
    await preview.screenshot({
      path: "test-results/bonded-assembly-preview.png",
    });
    await page.screenshot({ path: "test-results/bonded-assembly-field.png" });
  } finally {
    const project = await (await request.get("/api/project")).json();
    const restored = await request.post("/api/commands", {
      data: {
        expected_revision: project.revision,
        actor: "human",
        action: "restore",
        restore_revision: original.revision,
      },
    });
    expect(restored.ok(), await restored.text()).toBeTruthy();
  }
});
