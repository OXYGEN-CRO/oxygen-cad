import { expect, test, type APIRequestContext } from "@playwright/test";

async function completed(request: APIRequestContext, id: string) {
  let job: any;
  await expect
    .poll(
      async () => {
        job = await (await request.get(`/api/jobs/${id}`)).json();
        return job.state;
      },
      { timeout: 30000 },
    )
    .not.toMatch(/queued|running/);
  expect(job.state, job.message).toBe("succeeded");
  return job.result;
}

test("canonical viewport face remains associated through simulation mesh generation", async ({
  page,
  request,
}) => {
  const original = await (await request.get("/api/project")).json();
  try {
    const empty = {
      ...original,
      geometry_engine: "solidcraft",
      features: [],
      parameters: {},
      references: [],
      domains: {},
      native_document: null,
      material: null,
      requirements: [],
      placement: { translation: [0, 0, 0], rotation: [0, 0, 0] },
    };
    const reset = await request.post("/api/commands", {
      data: {
        expected_revision: original.revision,
        actor: "human",
        project: empty,
      },
    });
    expect(reset.ok(), await reset.text()).toBeTruthy();
    const revision = (await reset.json()).project.revision;
    const created = await request.post("/api/native/execute", {
      data: {
        expected_revision: revision,
        actor: "human",
        command: "solid.box",
        params: { length: 10, width: 20, height: 30, body_name: "Pick prism" },
      },
    });
    expect(created.ok(), await created.text()).toBeTruthy();
    const build = await request.post("/api/jobs/build");
    await completed(request, (await build.json()).id);
    const scene = (await (await request.get("/api/native/scene")).json()).scene;
    const bodyId = scene.bodies[0].id;
    await page.goto("/");
    const canvas = page.locator(".webgl canvas");
    await expect(canvas).toBeVisible();
    await expect(page.locator(".webgl[data-field-ready=true]")).toBeVisible();
    await page
      .getByRole("button", { name: "Orient view", exact: true })
      .click();
    await page.getByRole("menuitem", { name: "Top", exact: true }).click();
    const box = await canvas.boundingBox();
    await canvas.click({ position: { x: box!.width / 2, y: box!.height / 2 } });
    await page
      .getByLabel("Workspace", { exact: true })
      .selectOption("simulation");
    await page.getByRole("button", { name: "New study", exact: true }).click();
    await page
      .getByRole("button", { name: "Add support", exact: true })
      .click();
    await expect(page.locator(".sim-boundary")).toHaveCount(1);
    await page.getByRole("button", { name: "Save setup", exact: true }).click();
    await expect
      .poll(
        async () =>
          (await (await request.get("/api/project")).json()).domains.simulation
            ?.models[0].regions.length,
      )
      .toBe(1);
    const project = await (await request.get("/api/project")).json();
    const data = project.domains.simulation;
    const selected = data.models[0].regions[0];
    expect(selected.body_id).toBe(bodyId);
    expect(selected.selection).toMatch(new RegExp(`^${bodyId}:face:`));
    [5, 10, 30].forEach((value, axis) =>
      expect(selected.center_mm[axis]).toBeCloseTo(value, 6),
    );
    const job = await request.post("/api/domains/simulation/run", {
      data: {
        expected_revision: project.revision,
        actor: "human",
        operation: "mesh",
        input: {
          study_id: data.studies[0].id,
          mesh_size_mm: 6,
          refinements: 1,
        },
      },
    });
    expect(job.ok(), await job.text()).toBeTruthy();
    const result = await completed(request, (await job.json()).id);
    const mesh = result.project.domains.simulation.models[0].mesh;
    const region = mesh.regions[0];
    expect(region.selection).toBe(selected.selection);
    const nodes = [...new Set<number>(region.triangles.flat())].map(
      (id) => mesh.nodes[id],
    );
    expect(nodes.length).toBeGreaterThan(4);
    expect(nodes.every((point) => Math.abs(point[2] - 30) < 1e-7)).toBeTruthy();
    expect(region.area_mm2).toBeCloseTo(200, 6);
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
