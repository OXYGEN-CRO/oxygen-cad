import { test, expect } from "@playwright/test";
import { dataDefault } from "../src/workspaces/visualization/scene";
test.use({ actionTimeout: 15000 });

test("post-save draft edits survive delayed real save acknowledgements and still undo correctly", async ({
  page,
  request,
}) => {
  test.skip(
    process.env.O3D_VISUALIZATION_INTEGRATION !== "1",
    "Requires the integrated domain shell and disposable fixture",
  );
  test.setTimeout(60000);
  const original = await (await request.get("/api/project")).json();
  expect(original.name).toBe("Visualization browser fixture");
  await page.goto("/");
  await page
    .getByLabel("Workspace", { exact: true })
    .selectOption("visualization");
  const panel = page.getByRole("region", { name: "Visualization workspace" });
  const save = panel.getByRole("button", { name: "Save scene", exact: true });
  const width = panel.getByLabel("Render width", { exact: true });
  await expect(save).toBeEnabled({ timeout: 30000 });
  await panel
    .getByLabel("Scene name", { exact: true })
    .fill(`Acknowledged scene ${original.revision}`);
  await width.fill("128");
  let release!: () => void, committed!: () => void;
  const acknowledgement = new Promise<void>((resolve) => {
    release = resolve;
  });
  const transactionCommitted = new Promise<void>((resolve) => {
    committed = resolve;
  });
  let holding = true;
  // Delay only delivery: every request reaches the actual API/Store, and every
  // fulfilled response is the real server response. Polling is held too so the
  // editor definitely receives the save acknowledgement after the newer edit.
  await page.route("**/api/state", async (route) => {
    const response = await route.fetch();
    if (holding) await acknowledgement;
    await route.fulfill({ response });
  });
  await page.route("**/api/domains/visualization", async (route) => {
    if (route.request().method() !== "PUT" || !holding) {
      await route.continue();
      return;
    }
    const response = await route.fetch();
    expect(response.ok()).toBe(true);
    committed();
    await acknowledgement;
    await route.fulfill({ response });
  });
  try {
    await save.click();
    await transactionCommitted;
    expect(
      (await (await request.get("/api/project")).json()).domains.visualization
        .scenes[0].width,
    ).toBe(128);
    await width.fill("160");
  } finally {
    holding = false;
    release();
  }
  await expect(save).toBeEnabled({ timeout: 30000 });
  await page.waitForTimeout(1900);
  await expect(width).toHaveValue("160");
  expect(
    (await (await request.get("/api/project")).json()).domains.visualization
      .scenes[0].width,
  ).toBe(128);
  await save.click();
  await expect
    .poll(
      async () =>
        (await (await request.get("/api/project")).json()).domains.visualization
          .scenes[0].width,
    )
    .toBe(160);
  await expect(save).toBeEnabled({ timeout: 30000 });
  // An unchanged save must not leave a pending entry that swallows a later redo.
  const beforeNoop = (await (await request.get("/api/project")).json())
    .revision;
  await save.click();
  await expect
    .poll(
      async () => (await (await request.get("/api/project")).json()).revision,
    )
    .toBeGreaterThan(beforeNoop);
  await expect(save).toBeEnabled({ timeout: 30000 });
  await page
    .getByRole("button", { name: "Project tools", exact: true })
    .click();
  await page.getByRole("menuitem", { name: "Undo", exact: true }).click();
  await page
    .getByRole("button", { name: "Project tools", exact: true })
    .click();
  await page.getByRole("menuitem", { name: "Undo", exact: true }).click();
  await expect(width).toHaveValue("128");
  await page
    .getByRole("button", { name: "Project tools", exact: true })
    .click();
  await page.getByRole("menuitem", { name: "Redo", exact: true }).click();
  await expect(width).toHaveValue("160");
  const final = await (await request.get("/api/project")).json();
  expect(final.features).toEqual(original.features);
  expect(final.material).toEqual(original.material);
});

test("integrated Render workspace saves drafts, renders through real jobs, reopens, undoes and publishes", async ({
  page,
  request,
}) => {
  test.skip(
    process.env.O3D_VISUALIZATION_INTEGRATION !== "1",
    "Requires the integrated domain shell and disposable fixture",
  );
  test.setTimeout(120000);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const initial = await (await request.get("/api/project")).json();
  expect(initial.name).toBe("Visualization browser fixture");
  const initialBoardCount =
    initial.domains?.visualization?.storyboards?.length ?? 0;
  await page.goto("/");
  await page
    .getByLabel("Workspace", { exact: true })
    .selectOption("visualization");
  const panel = page.getByRole("region", { name: "Visualization workspace" });
  await expect(panel).toBeVisible();
  await expect(
    panel.getByRole("button", { name: "Save scene", exact: true }),
  ).toBeEnabled({ timeout: 30000 });
  const existingGround = panel.getByLabel("Ground stage in native render", {
    exact: true,
  });
  if (await existingGround.isVisible()) await existingGround.uncheck();
  await panel.getByLabel("Render backend").selectOption("cpu-raster");
  await panel.getByLabel("Scene name", { exact: true }).fill("Browser product");
  await panel.getByLabel("Camera distance mm", { exact: true }).fill("70");
  await panel.getByLabel("Camera target Z", { exact: true }).fill("6");
  await panel.getByLabel("Render width", { exact: true }).fill("128");
  await panel.getByLabel("Render height", { exact: true }).fill("96");
  await page.waitForTimeout(1900);
  await expect(panel.getByLabel("Scene name", { exact: true })).toHaveValue(
    "Browser product",
  );
  await panel.getByRole("button", { name: "Save scene", exact: true }).click();
  await expect
    .poll(
      async () =>
        (await (await request.get("/api/project")).json()).domains
          ?.visualization?.active_scene,
    )
    .toBe("Browser product");
  // Edits to an already-saved domain must also survive periodic application polling.
  await panel.getByLabel("Render width", { exact: true }).fill("160");
  await page.waitForTimeout(1900);
  await expect(panel.getByLabel("Render width", { exact: true })).toHaveValue(
    "160",
  );
  await panel.getByLabel("Render width", { exact: true }).fill("128");
  await panel.getByLabel("Render backend").selectOption("native-cpu-raytrace");
  await panel.getByLabel("Native samples per pixel", { exact: true }).fill("4");
  await panel.getByLabel("Native sampling seed", { exact: true }).fill("17");
  await panel
    .getByLabel("Ground stage in native render", { exact: true })
    .check();
  await panel.getByRole("button", { name: "Save scene", exact: true }).click();
  await expect
    .poll(
      async () =>
        (await (await request.get("/api/project")).json()).domains.visualization
          .scenes[0].renderer,
    )
    .toBe("native-cpu-raytrace");
  const saved = await (await request.get("/api/project")).json();
  await panel
    .getByRole("button", { name: "Render image", exact: true })
    .click();
  const render = panel.getByRole("img", {
    name: `Product render revision ${saved.revision}`,
    exact: true,
  });
  await expect(render).toBeVisible({ timeout: 30000 });
  expect(
    await render.evaluate((img: HTMLImageElement) => [
      img.naturalWidth,
      img.naturalHeight,
    ]),
  ).toEqual([128, 96]);
  const imagePath = await render.getAttribute("src");
  const jobs = (await (await request.get("/api/state")).json()).jobs;
  expect(
    jobs.some(
      (job: any) =>
        job.state === "succeeded" &&
        job.result?.renderer === "native-cpu-raytrace",
    ),
  ).toBe(true);
  expect((await (await request.get("/api/project")).json()).material).toEqual(
    initial.material,
  );
  await page.reload();
  await page
    .getByLabel("Workspace", { exact: true })
    .selectOption("visualization");
  await expect(panel.getByLabel("Scene name", { exact: true })).toHaveValue(
    "Browser product",
  );
  await expect(panel.locator(`img[src='${imagePath}']`)).toBeVisible({
    timeout: 30000,
  });
  await page
    .getByRole("button", { name: "Project tools", exact: true })
    .click();
  await page.getByRole("menuitem", { name: "Undo", exact: true }).click();
  await expect(panel.getByLabel("Render backend")).toHaveValue("cpu-raster");
  await page
    .getByRole("button", { name: "Project tools", exact: true })
    .click();
  await page.getByRole("menuitem", { name: "Redo", exact: true }).click();
  await expect(panel.getByLabel("Render backend")).toHaveValue(
    "native-cpu-raytrace",
  );
  // Publication deliberately uses the supported CPU raster presentation path.
  await expect(
    panel.getByRole("button", { name: "Save scene", exact: true }),
  ).toBeEnabled({ timeout: 30000 });
  await panel
    .getByLabel("Ground stage in native render", { exact: true })
    .uncheck();
  await panel.getByLabel("Render backend").selectOption("cpu-raster");
  await panel
    .getByRole("button", { name: "New storyboard", exact: true })
    .click();
  await panel.getByLabel("Duration seconds", { exact: true }).fill("1");
  await panel.getByLabel("Frames per second", { exact: true }).fill("10");
  await panel.getByLabel("Publication format").selectOption("png_sequence");
  await panel.getByRole("button", { name: "Save scene", exact: true }).click();
  await expect
    .poll(
      async () =>
        (await (await request.get("/api/project")).json()).domains.visualization
          .storyboards.length,
    )
    .toBe(initialBoardCount + 1);
  await expect(
    panel.getByRole("button", { name: "Publish animation", exact: true }),
  ).toBeEnabled({ timeout: 30000 });
  await panel
    .getByRole("button", { name: "Publish animation", exact: true })
    .click();
  const sequence = panel
    .getByRole("link", {
      name: "Download sequence",
      exact: true,
    })
    .first();
  await expect(sequence).toBeVisible({ timeout: 30000 });
  const href = await sequence.getAttribute("href");
  const response = await request.get(href!);
  expect(response.ok()).toBe(true);
  expect((await response.body()).subarray(0, 2).toString()).toBe("PK");
  const final = await (await request.get("/api/project")).json();
  expect(final.features).toEqual(initial.features);
  expect(final.material).toEqual(initial.material);
  expect(final.placement).toEqual(initial.placement);
  await page.screenshot({
    path: "test-results/visualization-integrated-publishing.png",
  });
  expect(errors).toEqual([]);
});

test("real visualization jobs cancel and retain exact stale snapshot renders after a concurrent geometry edit", async ({
  request,
}) => {
  test.skip(
    process.env.O3D_VISUALIZATION_INTEGRATION !== "1",
    "Requires the integrated domain job service and disposable fixture",
  );
  test.setTimeout(60000);
  const original = await (await request.get("/api/project")).json();
  expect(original.name).toBe("Visualization browser fixture");
  const data = structuredClone(
    original.domains?.visualization ?? dataDefault(),
  );
  const scene = data.scenes[0];
  scene.width = 640;
  scene.height = 480;
  scene.renderer = "cpu-raster";
  scene.ground = null;
  data.storyboards = [
    {
      name: "Cancellation fixture",
      scene: scene.name,
      duration: 10,
      fps: 20,
      keyframes: [
        { time: 0, camera: null, transforms: {}, hidden: null, callout: "" },
      ],
    },
  ];
  const submit = async (
    operation: string,
    input: unknown,
    revision: number,
  ) => {
    const response = await request.post("/api/domains/visualization/run", {
      data: { expected_revision: revision, actor: "human", operation, input },
    });
    expect(response.ok()).toBe(true);
    return response.json();
  };
  const job = await submit(
    "publish",
    { data, storyboard: data.storyboards[0].name, format: "png_sequence" },
    original.revision,
  );
  await expect
    .poll(
      async () =>
        (await (await request.get(`/api/jobs/${job.id}`)).json()).state,
      { timeout: 15000 },
    )
    .toBe("running");
  expect((await request.post(`/api/jobs/${job.id}/cancel`)).ok()).toBe(true);
  await expect
    .poll(
      async () =>
        (await (await request.get(`/api/jobs/${job.id}`)).json()).state,
      { timeout: 15000 },
    )
    .toBe("cancelled");
  const unchanged = await (await request.get("/api/project")).json();
  expect(unchanged).toEqual(original);
  scene.width = 512;
  scene.height = 512;
  scene.samples = 4;
  scene.seed = 17;
  scene.style = "shaded";
  scene.renderer = "native-cpu-raytrace";
  const reference = await submit("render", { data }, original.revision);
  await expect
    .poll(
      async () =>
        (await (await request.get(`/api/jobs/${reference.id}`)).json()).state,
      { timeout: 30000 },
    )
    .toBe("succeeded");
  const referenceResult = (
    await (await request.get(`/api/jobs/${reference.id}`)).json()
  ).result;
  const referenceBytes = await (
    await request.get("/api/artifacts/" + referenceResult.artifacts.image)
  ).body();
  const retained = await submit("render", { data }, original.revision);
  const updated = structuredClone(original);
  const extrusion = updated.features.find(
    (feature: any) => feature.type === "extrude",
  );
  expect(typeof extrusion.distance).toBe("number");
  extrusion.distance += 1;
  const save = await request.post("/api/commands", {
    data: {
      expected_revision: original.revision,
      actor: "human",
      project: updated,
      summary: "Concurrent render fixture geometry edit",
    },
  });
  expect(save.ok()).toBe(true);
  await expect
    .poll(
      async () =>
        (await (await request.get(`/api/jobs/${retained.id}`)).json()).state,
      { timeout: 30000 },
    )
    .toBe("succeeded");
  const completed = await (
    await request.get(`/api/jobs/${retained.id}`)
  ).json();
  expect(completed.result.stale).toBe(true);
  expect(completed.result.source_revision).toBe(original.revision);
  expect(completed.result.revision).toBe(original.revision);
  const png = await request.get(
    "/api/artifacts/" + completed.result.artifacts.image,
  );
  expect(png.ok()).toBe(true);
  const retainedBytes = await png.body();
  expect(retainedBytes.subarray(1, 4).toString()).toBe("PNG");
  expect(retainedBytes.equals(referenceBytes)).toBe(true);
  const final = await (await request.get("/api/project")).json();
  expect(final.features).toEqual(updated.features);
  expect(final.features).not.toEqual(original.features);
  expect(final.material).toEqual(original.material);
  expect(final.placement).toEqual(original.placement);
});
