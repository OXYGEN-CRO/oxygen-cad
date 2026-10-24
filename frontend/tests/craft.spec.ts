import { test, expect } from "@playwright/test";

test("precision workspace keeps filtering, disclosures, tool hints and standard views read-only", async ({
  page,
  request,
}) => {
  const before = await (await request.get("/api/project")).json();
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/");
  await expect(page.locator("canvas")).toBeVisible();
  await page
    .getByRole("button", { name: "Collapse feature sidebar", exact: true })
    .click();
  await page.keyboard.press("/");
  const search = page.getByRole("searchbox", { name: "Filter features" });
  await expect(search).toBeFocused();
  await search.fill("camera flange");
  await expect(page.locator(".feature-list .tree-item")).toHaveCount(1);
  await expect(page.locator(".feature-list .tree-item")).toContainText(
    "Camera flange",
  );
  await search.fill("missing-feature");
  await expect(page.locator(".feature-empty")).toBeVisible();
  await page.getByRole("button", { name: "Clear filter", exact: true }).click();
  await expect(page.locator(".feature-list .tree-item")).toHaveCount(
    before.features.length,
  );
  await page.locator(".feature-list .tree-item").first().click();
  await expect(page.getByLabel("Width", { exact: true })).toBeVisible();
  await expect(page.getByLabel("Feature name", { exact: true })).toBeHidden();
  await page.getByText("Feature details", { exact: true }).click();
  await page
    .getByLabel("Feature name", { exact: true })
    .fill("Draft precision part");
  await page.getByText("Feature details", { exact: true }).click();
  await page.getByText("Feature details", { exact: true }).click();
  await expect(page.getByLabel("Feature name", { exact: true })).toHaveValue(
    "Draft precision part",
  );
  await page.getByLabel("Width", { exact: true }).click();
  await page.keyboard.press("m");
  await expect(
    page.getByRole("button", {
      name: "Measure between two surface points",
      exact: true,
    }),
  ).toHaveAttribute("aria-pressed", "false");
  await page.locator(".panel-heading").first().click();
  await page.keyboard.press("m");
  await expect(
    page.getByRole("button", {
      name: "Measure between two surface points",
      exact: true,
    }),
  ).toHaveAttribute("aria-pressed", "true");
  await page.keyboard.press("Escape");
  await expect(
    page.getByRole("button", {
      name: "Measure between two surface points",
      exact: true,
    }),
  ).toHaveAttribute("aria-pressed", "false");
  // Standard views change the rendered CAD projection, without replacing the canvas.
  const canvas = await page.locator("canvas").elementHandle();
  const captures: Buffer[] = [];
  for (const view of ["Front", "Top", "Right", "Isometric"]) {
    await page
      .getByRole("button", { name: "Orient view", exact: true })
      .click();
    await page.getByRole("menuitem", { name: view, exact: true }).click();
    await page.evaluate(
      () =>
        new Promise((resolve) =>
          requestAnimationFrame(() => requestAnimationFrame(resolve)),
        ),
    );
    captures.push(await page.locator("canvas").screenshot());
    expect(
      await canvas!.evaluate(
        (element) => element === document.querySelector("canvas"),
      ),
    ).toBeTruthy();
    await expect(page.getByLabel("Feature name", { exact: true })).toHaveValue(
      "Draft precision part",
    );
  }
  for (let i = 1; i < captures.length; i++)
    expect(captures[i].equals(captures[i - 1])).toBeFalsy();
  await page.locator("canvas").hover();
  await page.mouse.wheel(0, -500);
  await page.evaluate(
    () =>
      new Promise((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(resolve)),
      ),
  );
  const zoomed = await page.locator("canvas").screenshot();
  await page.keyboard.press("f");
  await page.evaluate(
    () =>
      new Promise((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(resolve)),
      ),
  );
  const fitted = await page.locator("canvas").screenshot();
  expect(fitted.equals(zoomed)).toBeFalsy();
  expect(fitted.equals(captures.at(-1)!)).toBeTruthy();
  await page.getByRole("button", { name: "Fit model", exact: true }).hover();
  await expect(page.getByRole("tooltip")).toContainText("Fit model");
  await expect(page.getByRole("tooltip")).toContainText("F");
  expect(await (await request.get("/api/project")).json()).toEqual(before);
  expect(errors).toEqual([]);
});

test("save shortcut uses the same revision-protected command as the save action", async ({
  page,
  request,
}) => {
  const before = await (await request.get("/api/project")).json();
  await page.goto("/");
  await page.locator(".feature-list .tree-item").first().click();
  await page.getByText("Feature details", { exact: true }).click();
  const name = page.getByLabel("Feature name", { exact: true });
  await name.fill("Shortcut test part");
  await page.keyboard.press("ControlOrMeta+s");
  await expect(
    page.getByRole("button", { name: "Save changes", exact: true }),
  ).toHaveCount(0);
  const saved = await (await request.get("/api/project")).json();
  expect(saved.revision).toBe(before.revision + 1);
  expect(saved.features[0].name).toBe("Shortcut test part");
  // Leave this shared disposable fixture's feature name intact for subsequent flows.
  await name.fill(before.features[0].name);
  await page.keyboard.press("ControlOrMeta+s");
  await expect(
    page.getByRole("button", { name: "Save changes", exact: true }),
  ).toHaveCount(0);
  expect((await (await request.get("/api/project")).json()).features).toEqual(
    before.features,
  );
});

test("refined navigator opens canonical history and central engineering editors", async ({
  page,
  request,
}) => {
  const before = await (await request.get("/api/project")).json();
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  try {
    const seeded = await request.post("/api/commands", {
      data: {
        expected_revision: before.revision,
        actor: "human",
        project: {
          ...before,
          name: "Native navigator integration",
          geometry_engine: "solidcraft",
          native_document: null,
          domains: {},
          features: [],
          parameters: {},
          references: [],
          requirements: [],
          named_studies: {},
          active_study_id: "default",
          placement: { translation: [0, 0, 0], rotation: [0, 0, 0] },
        },
      },
    });
    expect(seeded.ok(), await seeded.text()).toBeTruthy();
    const seed = (await seeded.json()).project;
    const created = await request.post("/api/native/execute", {
      data: {
        expected_revision: seed.revision,
        actor: "human",
        command: "solid.box",
        params: { length: 20, width: 10, height: 5, name: "Navigator box" },
      },
    });
    expect(created.ok(), await created.text()).toBeTruthy();
    const canonical = (await created.json()).project;
    await page.goto("/");
    await expect(page.locator(".feature-count")).toHaveText(
      String(canonical.native_document.features.length),
    );
    await page
      .getByRole("searchbox", { name: "Filter features" })
      .fill("Navigator box");
    await page
      .locator(".feature-list")
      .getByRole("button", { name: "Navigator box", exact: true })
      .click();
    await expect(page.getByLabel("Workspace", { exact: true })).toHaveValue(
      "modeling",
    );
    await expect(page.getByLabel("Feature name", { exact: true })).toHaveValue(
      "Navigator box",
    );
    for (const [workspace, label] of [
      ["drawings", "Drawing workspace"],
      ["electronics", "Electronics workspace"],
    ]) {
      await page
        .getByLabel("Workspace", { exact: true })
        .selectOption(workspace);
      await expect(
        page
          .locator("#workspace-editor")
          .getByRole("region", { name: label, exact: true }),
      ).toBeVisible();
      await expect(page.locator("#inspector-sidebar")).toHaveCount(0);
    }
    const after = await (await request.get("/api/project")).json();
    expect(after.native_document).toEqual(canonical.native_document);
    expect(after.revision).toBe(canonical.revision);
    expect(errors).toEqual([]);
  } finally {
    const current = await (await request.get("/api/project")).json();
    const restored = await request.post("/api/commands", {
      data: {
        expected_revision: current.revision,
        actor: "human",
        action: "restore",
        restore_revision: before.revision,
      },
    });
    expect(restored.ok(), await restored.text()).toBeTruthy();
  }
});
