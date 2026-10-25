import { expect, test, type APIRequestContext } from "@playwright/test";

async function completed(request: APIRequestContext, id: string) {
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

test("new study preserves project material values, process and per-property provenance through the actual API", async ({
  page,
  request,
}) => {
  const original = await (await request.get("/api/project")).json();
  const material = {
    name: "Synthetic imported material",
    source: "Explicit roundtrip benchmark, no physical calibration",
    youngs_modulus_mpa: 200000,
    poisson_ratio: 0.3,
    density_g_cm3: 7.8,
    yield_strength_mpa: 250,
    thermal_conductivity_w_m_k: 20,
    specific_heat_j_kg_k: 900,
    thermal_expansion_per_k: 0.000012,
    process: "Recorded synthetic process",
    condition: "Recorded synthetic condition",
    property_sources: {
      youngs_modulus_mpa: "Elastic test declaration",
      thermal_conductivity_w_m_k: "Thermal test declaration",
      conductivity_w_mk: "Additional explicit citation, retained as declared",
      specific_heat_j_kg_k: "Heat-capacity declaration",
      thermal_expansion_per_k: "Expansion declaration",
    },
    plastic_curve: [
      { stress_mpa: 250, plastic_strain: 0 },
      { stress_mpa: 350, plastic_strain: 0.01 },
    ],
    plastic_curve_source: "Synthetic hardening declaration",
  };
  try {
    const changed = await request.post("/api/commands", {
      data: {
        expected_revision: original.revision,
        actor: "human",
        project: { ...original, domains: {}, material },
      },
    });
    expect(changed.ok(), await changed.text()).toBeTruthy();
    await page.goto("/");
    await page
      .getByLabel("Workspace", { exact: true })
      .selectOption("simulation");
    await page.getByRole("button", { name: "New study", exact: true }).click();
    await page.getByRole("button", { name: "Save setup", exact: true }).click();
    await expect
      .poll(
        async () =>
          (await (await request.get("/api/project")).json()).domains.simulation
            ?.materials?.length,
      )
      .toBe(1);
    const saved = await (await request.get("/api/project")).json();
    const card = saved.domains.simulation.materials[0];
    expect(card.youngs_modulus_mpa).toBe(material.youngs_modulus_mpa);
    expect(card.density_g_cm3).toBe(material.density_g_cm3);
    expect(card.conductivity_w_mk).toBe(material.thermal_conductivity_w_m_k);
    expect(card.specific_heat_j_kgk).toBe(material.specific_heat_j_kg_k);
    expect(card.expansion_per_k).toBe(material.thermal_expansion_per_k);
    expect(card.plastic_curve).toEqual([
      [250, 0],
      [350, 0.01],
    ]);
    expect(card.plastic_source).toBe(material.plastic_curve_source);
    expect(card.process).toBe(material.process);
    expect(card.condition).toBe(material.condition);
    expect(card.property_sources).toEqual({
      ...material.property_sources,
      youngs_modulus_mpa: material.property_sources.youngs_modulus_mpa,
      conductivity_w_mk: material.property_sources.conductivity_w_mk,
      specific_heat_j_kgk: material.property_sources.specific_heat_j_kg_k,
      expansion_per_k: material.property_sources.thermal_expansion_per_k,
    });
    expect(saved.material).toEqual(material);
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

for (const studyType of [
  "linear_static",
  "steady_thermal",
  "thermal_stress",
] as const) {
  const thermal = studyType === "steady_thermal";
  const coupled = studyType === "thermal_stress";
  test(`native body meshes and solves canonical quadratic ${studyType} and displays the actual field`, async ({
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
            name: "Disposable canonical quadratic acceptance",
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
      const created = await request.post("/api/native/execute", {
        data: {
          expected_revision: (await reset.json()).project.revision,
          actor: "human",
          command: "solid.box",
          params: {
            length: 10,
            width: 2,
            height: 2,
            body_name: "Numerical block",
          },
        },
      });
      expect(created.ok(), await created.text()).toBeTruthy();
      await completed(
        request,
        (await (await request.post("/api/jobs/build")).json()).id,
      );
      const state = await (await request.get("/api/state")).json();
      const body = state.build.scene.bodies[0];
      const face = (axis: number, sign: number) =>
        body.faces.find((f: any) => f.normal && sign * f.normal[axis] > 0.99);
      const selected = {
        root: face(0, -1),
        tip: face(0, 1),
        y: face(1, -1),
        z: face(2, -1),
      };
      expect(Object.values(selected).every(Boolean)).toBeTruthy();
      const project = await (await request.get("/api/project")).json();
      const data = {
        schema_version: 1,
        active_study_id: "acceptance",
        models: [
          {
            id: "model",
            name: "Actual native block",
            geometry_key: state.geometry_key,
            body_ids: [body.id],
            regions: Object.entries(selected).map(([id, f]: [string, any]) => ({
              id,
              body_id: body.id,
              selection: f.id,
            })),
          },
        ],
        materials: [
          {
            id: "synthetic",
            name: "Synthetic numerical material",
            source: "Declared browser benchmark, no production calibration",
            ...(thermal
              ? { conductivity_w_mk: 20 }
              : {
                  youngs_modulus_mpa: 200000,
                  poisson_ratio: 0.3,
                  density_g_cm3: 7.8,
                  ...(coupled
                    ? { conductivity_w_mk: 20, expansion_per_k: 0.000012 }
                    : {}),
                }),
          },
        ],
        studies: [
          {
            id: "acceptance",
            name: "Canonical quadratic acceptance",
            type: studyType,
            ...(coupled ? { stress_free_temperature_k: 300 } : {}),
            model_id: "model",
            assignments: [{ body_id: body.id, material_id: "synthetic" }],
            assumptions_confirmed: true,
            settings: thermal
              ? {}
              : { probe_point_mm: [10, 1, 1], probe_body_id: body.id },
            cases: thermal
              ? [
                  {
                    id: "thermal",
                    name: "Thermal SI reference",
                    thermal: [
                      {
                        id: "cold",
                        kind: "temperature",
                        region_id: "root",
                        value: 300,
                        source: "Declared 300 K thermostat",
                      },
                      {
                        id: "hot",
                        kind: "temperature",
                        region_id: "tip",
                        value: 400,
                        source: "Declared 400 K thermostat",
                      },
                    ],
                  },
                ]
              : [
                  {
                    id: coupled ? "thermoelastic" : "axial",
                    name: coupled
                      ? "Thermoelastic SI reference"
                      : "Axial SI reference",
                    ...(coupled
                      ? {
                          thermal: [
                            {
                              id: "uniform-hot",
                              kind: "temperature",
                              region_id: "root",
                              value: 400,
                              source: "Declared 400 K thermostat",
                            },
                          ],
                        }
                      : {}),
                    supports: [
                      {
                        id: "sx",
                        region_id: "root",
                        kind: "partial",
                        components: [true, false, false],
                      },
                      {
                        id: "sy",
                        region_id: "y",
                        kind: "partial",
                        components: [false, true, false],
                      },
                      {
                        id: "sz",
                        region_id: "z",
                        kind: "partial",
                        components: [false, false, true],
                      },
                    ],
                    loads: coupled
                      ? []
                      : [
                          {
                            id: "force",
                            kind: "force",
                            region_id: "tip",
                            vector: [100, 0, 0],
                            source: "Declared 100 N analytical force",
                          },
                        ],
                  },
                ],
          },
        ],
      };
      const saved = await request.put("/api/domains/simulation", {
        data: { expected_revision: project.revision, actor: "human", data },
      });
      expect(saved.ok(), await saved.text()).toBeTruthy();
      await page.goto("/");
      await page
        .getByLabel("Workspace", { exact: true })
        .selectOption("simulation");
      await expect(
        page.getByLabel("Element family", { exact: true }),
      ).toHaveValue("C3D10");
      if (!thermal)
        await expect(
          page.getByLabel("Displacement probe (mm)", { exact: true }),
        ).toHaveValue("10, 1, 1");
      if (coupled)
        await expect(
          page.getByLabel("Stress-free temperature (K)", { exact: true }),
        ).toHaveValue("300");
      await page.getByLabel("Initial size (mm)", { exact: true }).fill("1.5");
      await page.getByLabel("Mesh levels", { exact: true }).fill("2");
      await page
        .getByRole("button", { name: "Generate mesh", exact: true })
        .click();
      await expect
        .poll(
          async () =>
            (await (await request.get("/api/project")).json()).domains
              .simulation.models[0].mesh?.element_family,
          { timeout: 120000 },
        )
        .toBe("C3D10");
      await expect(
        page.getByRole("button", { name: "Solve all cases", exact: true }),
      ).toBeEnabled();
      await page
        .getByRole("button", { name: "Solve all cases", exact: true })
        .click();
      await expect(
        page.getByText(
          `${thermal ? "Thermal" : coupled ? "Thermoelastic" : "Axial"} SI reference · verified numerically`,
          {
            exact: true,
          },
        ),
      ).toBeVisible({ timeout: 120000 });
      const fieldLink = page.getByRole("link", {
        name: "Field JSON",
        exact: true,
      });
      const fieldResponse = await request.get(
        (await fieldLink.getAttribute("href"))!,
      );
      expect(fieldResponse.ok()).toBeTruthy();
      const field = await fieldResponse.json();
      expect(
        Object.values(field.element_connectivity).every(
          (cell: any) => cell.length === 10,
        ),
      ).toBeTruthy();
      if (thermal) {
        for (const [n, value] of Object.entries(field.temperature_k)) {
          expect(value as number).toBeCloseTo(300 + 10 * field.nodes[n][0], 5);
        }
      } else {
        expect(
          Object.values(field.displacements_mm).reduce(
            (m: number, v: any) => Math.max(m, v[0]),
            0,
          ),
        ).toBeCloseTo(coupled ? 0.012 : 0.00125, 7);
      }
      if (coupled) {
        expect(
          Object.values(field.temperature_k).every((t: any) => t === 400),
        ).toBeTruthy();
        expect(Object.keys(field.integration_point_stress_mpa).length).toBe(
          Object.keys(field.element_connectivity).length,
        );
      }
      const cornerNodes = new Set<number>(
        (Object.values(field.element_connectivity) as number[][]).flatMap(
          (cell) => cell.slice(0, 4),
        ),
      );
      expect(
        field.boundary_triangles.some((triangle: number[]) =>
          triangle.some((n) => !cornerNodes.has(n)),
        ),
      ).toBeTruthy();
      await page
        .getByRole("button", { name: "View numerical field", exact: true })
        .click();
      await expect(
        page.getByLabel("Field quantity", { exact: true }),
      ).toHaveValue(thermal ? "temperature" : "displacement");
      const preview = page.getByLabel(
        "Numerical finite element field preview",
        {
          exact: true,
        },
      );
      await preview.scrollIntoViewIfNeeded();
      await expect(preview.locator("canvas")).toBeVisible();
      await expect(preview).toBeInViewport();
      await preview.screenshot({
        path: `test-results/canonical-quadratic-${studyType}-preview.png`,
      });
      await page.screenshot({
        path: `test-results/canonical-quadratic-${studyType}-field.png`,
      });
    } finally {
      const current = await (await request.get("/api/project")).json();
      const restore = await request.post("/api/commands", {
        data: {
          expected_revision: current.revision,
          actor: "human",
          action: "restore",
          restore_revision: original.revision,
        },
      });
      expect(restore.ok(), await restore.text()).toBeTruthy();
    }
  });
}
