import { expect, test, type APIRequestContext } from "@playwright/test";
async function complete(
  request: APIRequestContext,
  id: string,
  timeout = 120000,
) {
  let job: any;
  await expect
    .poll(
      async () => {
        job = await (await request.get(`/api/jobs/${id}`)).json();
        return job.state;
      },
      { timeout },
    )
    .not.toMatch(/queued|running/);
  expect(job.state, job.message).toBe("succeeded");
  return job.result;
}

test("native solids preserve explicit experimental contact and downloadable evidence", async ({
  page,
  request,
}) => {
  // Two real multi-trial solves can exceed UI polling deadlines on CI runners.
  // Follow their actual job states; a failed/cancelled job still fails immediately.
  test.setTimeout(1320000);
  const original = await (await request.get("/api/project")).json();
  try {
    const reset = await request.post("/api/commands", {
      data: {
        expected_revision: original.revision,
        actor: "human",
        project: {
          ...original,
          name: "Disposable contact assembly reference",
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
    const built = await complete(
      request,
      (await (await request.post("/api/jobs/build")).json()).id,
    );
    const state = await (await request.get("/api/state")).json();
    expect(built.key).toBe(state.geometry_key);
    const bodies = built.scene.bodies;
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
          name: "Native contact pair",
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
          name: "Actual surface contact",
          type: "nonlinear_static",
          model_id: "model",
          assignments: [first, second].map((body) => ({
            body_id: body.id,
            material_id: "synthetic",
          })),
          cases: [
            {
              id: "pull",
              name: "Contact SI reference",
              supports: [
                { id: "root", region_id: "root" },
                {
                  id: "guide",
                  region_id: "tip",
                  kind: "partial",
                  components: [false, true, true],
                },
              ],
              loads: [
                {
                  id: "force",
                  region_id: "tip",
                  vector: [-100, 0, 0],
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
    await page.getByText("Surface contact", { exact: true }).click();
    await page
      .getByRole("button", { name: "Add surface contact", exact: true })
      .click();
    await page
      .getByLabel("Contact 1 primary region", { exact: true })
      .selectOption("primary");
    await page
      .getByLabel("Contact 1 secondary region", { exact: true })
      .selectOption("secondary");
    await page
      .getByLabel("Contact 1 Initial contact geometry tolerance (mm)", {
        exact: true,
      })
      .fill("0.0000001");
    await page
      .getByLabel("Contact 1 source", { exact: true })
      .fill("Declared frictionless contact verification parameters");
    await page
      .getByLabel("Contact 1 Normal penalty (N/mm³)", { exact: true })
      .fill("10000000");
    await page
      .getByLabel("Contact 1 Friction coefficient (0 for frictionless)", {
        exact: true,
      })
      .fill("0");
    await page
      .getByLabel("Contact 1 Maximum permitted penetration (mm)", {
        exact: true,
      })
      .fill("0.0001");
    await page.getByRole("button", { name: "Save setup", exact: true }).click();
    await expect
      .poll(
        async () =>
          (await (await request.get("/api/project")).json()).domains.simulation
            .studies[0].interfaces[0]?.source,
      )
      .toBe("Declared frictionless contact verification parameters");
    await expect(
      page.getByRole("status").filter({ hasText: /^Saving/ }),
    ).toHaveCount(0);
    const saved = await (await request.get("/api/project")).json();
    expect(saved.domains.simulation.studies[0].interfaces[0].formulation).toBe(
      "surface_to_surface_penalty",
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
    const initialSubmission = page.waitForResponse(
      (response) =>
        response.url().endsWith("/api/domains/simulation/run") &&
        response.request().method() === "POST" &&
        response.request().postDataJSON()?.operation === "solve",
    );
    await page
      .getByRole("button", { name: "Solve all cases", exact: true })
      .click();
    await complete(
      request,
      (await (await initialSubmission).json()).id,
      600000,
    );
    await expect(
      page.getByText("Contact SI reference · inconclusive", {
        exact: true,
      }),
    ).toBeVisible();
    const link = page.getByRole("link", {
      name: "Contact interface fields",
      exact: true,
    });
    const response = await request.get((await link.getAttribute("href"))!);
    expect(response.ok()).toBeTruthy();
    const evidence = await response.json();
    expect(evidence.pairs).toHaveLength(1);
    expect(evidence.pairs[0].normal_penalty_n_mm3).toBe(10000000);
    expect(evidence.pairs[0].friction_coefficient).toBe(0);
    expect(
      evidence.checks.at(-1).pairs[evidence.pairs[0].id].maximum_pressure_mpa,
    ).toBeCloseTo(25, 4);
    expect(evidence.checks.at(-1).force_balance_error).toBeLessThan(1e-5);
    expect(evidence.work_audit.frictional_work_integrated).toBe(true);
    expect(evidence.work_audit.contact_tangent_stability_qualified).toBe(false);
    expect(evidence.work_audit.energy_balance_error).toBeLessThan(1e-5);
    expect(evidence.current_state_audit.bulk_kernel.checked).toBe(true);
    expect(evidence.current_state_audit.minimum_normal_restraint_rank).toBe(12);
    expect(evidence.current_state_audit.minimum_constitutive_rank).toBe(12);
    expect(evidence.current_state_audit.sliding_frames).toBe(0);
    expect(evidence.current_state_audit.full_frozen_operator.performed).toBe(
      true,
    );
    expect(
      evidence.current_state_audit.full_frozen_operator.complete_frames,
    ).toBe(evidence.current_state_audit.frames);
    expect(
      evidence.current_state_audit.full_frozen_operator.free_dofs,
    ).toBeGreaterThan(0);
    expect(
      evidence.current_state_audit.full_frozen_operator
        .full_contact_stability_qualified,
    ).toBe(false);
    expect(evidence.current_state_audit.full_contact_stability_qualified).toBe(
      false,
    );
    await page
      .getByText("Contact restraint during loading", { exact: true })
      .click();
    await expect(
      page.getByText(
        "These restraint checks do not establish full contact stability.",
        { exact: false },
      ),
    ).toBeVisible();
    expect(evidence.work_audit.raw_telemetry_sha256).toMatch(/^[a-f0-9]{64}$/);
    expect(evidence.work_history.at(-1).bulk_internal_energy_n_mm).toBeCloseTo(
      0.125,
      5,
    );
    expect(evidence.work_history.at(-1).contact_spring_energy_n_mm).toBeCloseTo(
      0.000125,
      7,
    );
    expect(Math.abs(evidence.frictional_dissipation_n_mm)).toBeLessThan(1e-12);
    await expect(
      page.getByText("contact active-set tangent stability not qualified", {
        exact: false,
      }),
    ).toBeVisible();
    const fieldResponse = await request.get(
      (await page
        .getByRole("link", { name: "Field JSON", exact: true })
        .getAttribute("href"))!,
    );
    const field = await fieldResponse.json();
    expect(field.contact_components[0]).toBe("clipped_normal_gap_mm");
    expect(field.contact_gap_convention).toContain(
      "positive opening is clipped",
    );
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
      .selectOption("contact_pressure");
    await expect(
      page.getByLabel("Field color range", { exact: true }),
    ).toHaveText("Uniform at displayed precision: 25.0000 MPa");
    await preview.screenshot({
      path: "test-results/contact-assembly-preview.png",
    });
    await page.screenshot({ path: "test-results/contact-assembly-field.png" });

    // Declare a guided simple-shear numerical fixture on both actual meshes.
    // These are explicit nodal guides, not inferred mounting conditions.
    const beforeCycle = await (await request.get("/api/project")).json();
    const cycleData = beforeCycle.domains.simulation;
    const cycleStudy = cycleData.studies[0];
    const cycleCase = cycleStudy.cases[0];
    for (const mesh of [
      cycleData.models[0].mesh,
      ...cycleData.models[0].refinements,
    ]) {
      for (const [id, body] of [
        ["first-guide", first],
        ["second-guide", second],
      ] as const) {
        mesh.regions.push({
          id,
          body_id: body.id,
          node_ids: [
            ...new Set(
              mesh.elements
                .filter((element: any) => element.body_id === body.id)
                .flatMap((element: any) => element.nodes),
            ),
          ],
        });
      }
    }
    cycleCase.supports.find(
      (support: any) => support.id === "guide",
    ).region_id = "second-guide";
    cycleCase.supports.push({
      id: "shear-guide",
      region_id: "first-guide",
      kind: "partial",
      components: [true, false, true],
    });
    cycleCase.loads[0].source =
      "Declared 100 N normal preload held during the shear cycle";
    cycleCase.loads.push({
      id: "shear",
      region_id: "primary",
      vector: [0, 100, 0],
      source: "Declared symmetric simple-shear cycle, no measured friction",
    });
    const cycleSave = await request.put("/api/domains/simulation", {
      data: {
        expected_revision: beforeCycle.revision,
        actor: "human",
        data: cycleData,
      },
    });
    expect(cycleSave.ok(), await cycleSave.text()).toBeTruthy();
    await page.reload();
    await page
      .getByLabel("Workspace", { exact: true })
      .selectOption("simulation");
    await page
      .getByLabel("Load 1 history (time, multiplier)", { exact: true })
      .fill("0, 0\n1, 1\n4, 1");
    await page
      .getByRole("button", {
        name: "Apply Load 1 history (time, multiplier)",
        exact: true,
      })
      .click();
    await page
      .getByLabel("Load 2 history (time, multiplier)", { exact: true })
      .fill("0, 0\n1, 0\n2, 1\n3, -1\n4, 0");
    await page
      .getByRole("button", {
        name: "Apply Load 2 history (time, multiplier)",
        exact: true,
      })
      .click();
    await page.getByText("Surface contact", { exact: true }).click();
    await page
      .getByLabel("Contact 1 source", { exact: true })
      .fill(
        "Declared Coulomb 0.3/stick slope100000 reference, no interface calibration",
      );
    await page
      .getByLabel("Contact 1 Friction coefficient (0 for frictionless)", {
        exact: true,
      })
      .fill("0.3");
    await page
      .getByLabel(
        "Contact 1 Tangential penalty (N/mm³, positive friction only)",
        { exact: true },
      )
      .fill("100000");
    await page.getByRole("button", { name: "Save setup", exact: true }).click();
    await expect
      .poll(
        async () =>
          (await (await request.get("/api/project")).json()).domains.simulation
            .studies[0].cases[0].loads[1].history,
      )
      .toEqual([
        [0, 0],
        [1, 0],
        [2, 1],
        [3, -1],
        [4, 0],
      ]);
    const cycleSubmission = page.waitForResponse(
      (response) =>
        response.url().endsWith("/api/domains/simulation/run") &&
        response.request().method() === "POST" &&
        response.request().postDataJSON()?.operation === "solve",
    );
    await page
      .getByRole("button", { name: "Solve all cases", exact: true })
      .click();
    await complete(request, (await (await cycleSubmission).json()).id, 600000);
    let cycleEvidence: any;
    await expect
      .poll(
        async () => {
          const currentLink = page.getByRole("link", {
            name: "Contact interface fields",
            exact: true,
          });
          if ((await currentLink.count()) !== 1) return 0;
          const href = await currentLink.getAttribute("href");
          if (!href) return 0;
          cycleEvidence = await (await request.get(href)).json();
          return cycleEvidence.work_history?.at(-1)?.time_s ?? 0;
        },
        { timeout: 10000 },
      )
      .toBe(4);
    expect(cycleEvidence.work_audit.energy_balance_error).toBeLessThan(0.02);
    expect(cycleEvidence.frictional_dissipation_n_mm).toBeCloseTo(0.17625, 3);
    expect(cycleEvidence.current_state_audit.bulk_kernel.checked).toBe(true);
    expect(
      cycleEvidence.current_state_audit.minimum_normal_restraint_rank,
    ).toBe(12);
    expect(cycleEvidence.current_state_audit.minimum_constitutive_rank).toBe(
      12,
    );
    expect(cycleEvidence.current_state_audit.sliding_frames).toBeGreaterThan(0);
    expect(
      cycleEvidence.current_state_audit.full_contact_stability_qualified,
    ).toBe(false);
    expect(
      cycleEvidence.current_state_history.some(
        (row: any) => row.maximum_local_tangent_asymmetry > 0.1,
      ),
    ).toBe(true);
    const restraintSummary = page.getByText(
      "Contact restraint during loading",
      { exact: true },
    );
    const restraintDetails = restraintSummary.locator("..");
    if (!(await restraintDetails.evaluate((node) => node.hasAttribute("open"))))
      await restraintSummary.click();
    await expect(restraintDetails).toContainText(
      `Sliding increments: ${cycleEvidence.current_state_audit.sliding_frames}`,
    );
    expect(
      cycleEvidence.current_state_audit.full_frozen_operator.performed,
    ).toBe(true);
    await expect(restraintDetails).toContainText("Full deformable model:");
    await expect(restraintDetails).toContainText(
      "a small estimate cannot prove stability",
    );
    for (const [time, shear] of [
      [1, 0],
      [2, 30],
      [3, -30],
      [4, 30],
    ]) {
      const phase = cycleEvidence.work_history.find(
        (row: any) => Math.abs(row.time_s - time) < 1e-7,
      );
      expect(phase.load_multipliers.force).toBe(1);
      const force = Object.values(phase.secondary_force_n)[0] as number[];
      expect(force[0]).toBeCloseTo(100, 3);
      expect(force[1]).toBeCloseTo(shear, 3);
    }
    await expect(
      page.getByText("contact active-set tangent stability not qualified", {
        exact: false,
      }),
    ).toBeVisible();
    await page.screenshot({ path: "test-results/contact-cycle-history.png" });
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
