import { expect, test } from "@playwright/test";

test("author, issue and reopen a measured drawing through the shared workspace", async ({ page, request }) => {
  const current = await (await request.get("/api/project")).json();
  const project = { ...current, name: "Drawing browser fixture", geometry_engine: "occt", native_document: null, domains: {}, parameters: {}, references: [],
    placement: { translation: [0, 0, 0], rotation: [0, 0, 0] },
    features: [
      { id: "profile", type: "profile", plane: "XY", width: 60, height: 40 },
      { id: "plate", type: "extrude", profile: "profile", distance: 10 },
      { id: "bores", type: "hole", axis: "Z", center: [-10, 0, 0], diameter: 10, columns: 2, spacing: [20, 0] },
    ] };
  expect((await request.post("/api/commands", { data: { expected_revision: current.revision, actor: "human", action: "replace", project, summary: "Disposable drawing acceptance fixture" } })).ok()).toBeTruthy();
  const build = await (await request.post("/api/jobs/build")).json();
  await expect.poll(async () => (await (await request.get(`/api/jobs/${build.id}`)).json()).state).toBe("succeeded");
  await page.goto("/");
  await page.getByLabel("Workspace", { exact: true }).selectOption("drawings");
  await page.getByRole("button", { name: "New document", exact: true }).click();
  await page.getByLabel("Title", { exact: true }).fill("Supplier review plate");
  // Background project polling must preserve an unsaved graphical edit.
  await page.waitForTimeout(2000);
  await expect(page.getByLabel("Title", { exact: true })).toHaveValue("Supplier review plate");
  await page.getByLabel("Orientation", { exact: true }).selectOption("top");
  await page.getByText("Dimensions and manufacturing annotations", { exact: true }).click();
  await page.getByLabel("Annotation", { exact: true }).selectOption("diameter");
  await page.getByRole("button", { name: "Add attached annotation", exact: true }).click();
  await expect(page.getByText("diameter · bores", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByText("Drawing intent saved", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Publish PDF / DXF", exact: true }).click();
  await expect(page.getByRole("link", { name: "Open PDF", exact: true })).toBeVisible({ timeout: 30000 });
  const pdfUrl = await page.getByRole("link", { name: "Open PDF", exact: true }).getAttribute("href");
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
  await expect(page.getByLabel("Title", { exact: true })).toHaveValue("Supplier review plate");
  expect(await page.getByLabel("Paper size").inputValue()).toBe("A4");
});
