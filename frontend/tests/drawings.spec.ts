import { expect, test } from "@playwright/test";

test("author, issue and reopen a measured drawing through the shared workspace", async ({
  page,
  request,
}) => {
  const current = await (await request.get("/api/project")).json();
  const project = {
    ...current,
    name: "Drawing browser fixture",
    geometry_engine: "occt",
    native_document: null,
    domains: {},
    parameters: {},
    references: [],
    placement: { translation: [0, 0, 0], rotation: [0, 0, 0] },
    features: [
      { id: "profile", type: "profile", plane: "XY", width: 60, height: 40 },
      { id: "plate", type: "extrude", profile: "profile", distance: 10 },
      {
        id: "bores",
        type: "hole",
        axis: "Z",
        center: [-10, 0, 0],
        diameter: 10,
        columns: 2,
        spacing: [20, 0],
      },
    ],
  };
  expect(
    (
      await request.post("/api/commands", {
        data: {
          expected_revision: current.revision,
          actor: "human",
          action: "replace",
          project,
          summary: "Disposable drawing acceptance fixture",
        },
      })
    ).ok(),
  ).toBeTruthy();
  const build = await (await request.post("/api/jobs/build")).json();
  await expect
    .poll(
      async () =>
        (await (await request.get(`/api/jobs/${build.id}`)).json()).state,
    )
    .toBe("succeeded");
  await page.goto("/");
  await page.getByLabel("Workspace", { exact: true }).selectOption("drawings");
  await page.getByRole("button", { name: "New document", exact: true }).click();
  await page.getByLabel("Title", { exact: true }).fill("Supplier review plate");
  // Background project polling must preserve an unsaved graphical edit.
  await page.waitForTimeout(2000);
  await expect(page.getByLabel("Title", { exact: true })).toHaveValue(
    "Supplier review plate",
  );
  await page
    .getByRole("combobox", { name: /^Orientation/ })
    .selectOption("top");
  await page
    .getByText("Dimensions and manufacturing annotations", { exact: true })
    .click();
  await page
    .getByRole("combobox", { name: /^Annotation/ })
    .selectOption("diameter");
  await page
    .getByRole("button", { name: "Add attached annotation", exact: true })
    .click();
  await expect(
    page.getByText("diameter · bores", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(
    page.getByText("Drawing intent saved", { exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Publish PDF / DXF", exact: true })
    .click();
  await expect(
    page.getByRole("link", { name: "Open PDF", exact: true }),
  ).toBeVisible({ timeout: 30000 });
  const pdfUrl = await page
    .getByRole("link", { name: "Open PDF", exact: true })
    .getAttribute("href");
  const pdf = await request.get(pdfUrl!);
  expect(pdf.headers()["content-type"]).toContain("pdf");
  expect((await pdf.body()).subarray(0, 8).toString()).toBe("%PDF-1.7");
  const saved = await (await request.get("/api/project")).json();
  const doc = saved.domains.drawings.documents[0];
  expect(doc.title).toBe("Supplier review plate");
  expect(doc.sheets[0].views[0].orientation).toBe("top");
  expect(doc.sheets[0].annotations[0].references[0].target).toBe("bores");
  await page.reload();
  await page.getByLabel("Workspace", { exact: true }).selectOption("drawings");
  await expect(page.getByLabel("Title", { exact: true })).toHaveValue(
    "Supplier review plate",
  );
  expect(await page.getByLabel("Paper size").inputValue()).toBe("A4");
});

test("a named native diameter follows an agent edit and graphical association refresh", async ({
  page,
  request,
}) => {
  let current = await (await request.get("/api/project")).json();
  const reset = await request.post("/api/commands", {
    data: {
      expected_revision: current.revision,
      actor: "agent",
      action: "replace",
      project: {
        ...current,
        name: "Native drawing fixture",
        geometry_engine: "solidcraft",
        native_document: null,
        domains: {},
        features: [],
        parameters: {},
        references: [],
      },
      summary: "Disposable native drawing fixture",
    },
  });
  expect(reset.ok()).toBeTruthy();
  current = await (await request.get("/api/project")).json();
  expect(
    (
      await request.post("/api/native/execute", {
        data: {
          expected_revision: current.revision,
          actor: "agent",
          command: "solid.cylinder",
          params: { radius: 5, height: 10, body_name: "Shaft" },
        },
      })
    ).ok(),
  ).toBeTruthy();
  const build = await (await request.post("/api/jobs/build")).json();
  await expect
    .poll(
      async () =>
        (await (await request.get(`/api/jobs/${build.id}`)).json()).state,
    )
    .toBe("succeeded");
  await page.goto("/");
  await page.getByLabel("Workspace", { exact: true }).selectOption("drawings");
  await page.getByRole("button", { name: "New document", exact: true }).click();
  await page
    .getByRole("combobox", { name: /^Orientation/ })
    .selectOption("top");
  await page
    .getByText("Dimensions and manufacturing annotations", { exact: true })
    .click();
  await page
    .getByRole("button", { name: "Load model references", exact: true })
    .click();
  const picker = page.getByLabel("Model reference 1", { exact: true });
  await expect(picker).toBeVisible();
  const side = await picker
    .getByRole("option", { name: "Shaft · F1:side#2", exact: true })
    .getAttribute("value");
  await picker.selectOption(side!);
  await page
    .getByRole("button", { name: "Add attached annotation", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Publish PDF / DXF", exact: true })
    .click();
  await expect(
    page.getByRole("link", { name: "Open PDF", exact: true }),
  ).toBeVisible({ timeout: 30000 });
  current = await (await request.get("/api/project")).json();
  const face =
    current.domains.drawings.documents[0].sheets[0].annotations[0]
      .references[0];
  expect(face.kind).toBe("native_face");
  const edited = await request.post("/api/native/execute", {
    data: {
      expected_revision: current.revision,
      actor: "agent",
      command: "timeline.edit",
      params: {
        feature: current.native_document.features[0].id,
        set: { radius: "8" },
      },
    },
  });
  expect(edited.ok()).toBeTruthy();
  await expect(
    page.getByText("Source changed · refresh required", { exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Refresh associations", exact: true })
    .click();
  await expect(
    page.getByText("Source changed · refresh required", { exact: true }),
  ).not.toBeVisible({ timeout: 30000 });
  await page
    .getByRole("button", { name: "Publish PDF / DXF", exact: true })
    .click();
  await expect(
    page.getByRole("link", { name: "Open PDF", exact: true }),
  ).toBeVisible({ timeout: 30000 });
  const preview = page.getByLabel("Exact drawing preview", { exact: true });
  const path = await preview.getAttribute("data");
  expect(await (await request.get(path!)).text()).toContain("⌀16.00 ±0.02");
});
