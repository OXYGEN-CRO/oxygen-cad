import { test, expect } from "@playwright/test";

let originalProject: any;
test.beforeEach(async ({ request }) => {
  originalProject = await (await request.get("/api/project")).json();
});
test.afterEach(async ({ request }) => {
  const current = await (await request.get("/api/project")).json();
  const response = await request.post("/api/commands", {
    data: {
      expected_revision: current.revision,
      actor: "human",
      project: originalProject,
    },
  });
  expect(response.ok(), await response.text()).toBeTruthy();
});

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

test("edits and a save shortcut during acknowledgement preserve both revisions", async ({
  page,
  request,
}) => {
  const before = await (await request.get("/api/project")).json();
  await page.goto("/");
  await page.locator(".feature-list .tree-item").first().click();
  await page.getByText("Feature details", { exact: true }).click();
  const name = page.getByLabel("Feature name", { exact: true });
  let acknowledge: () => void = () => {};
  const held = new Promise<void>((resolve) => (acknowledge = resolve));
  let arrived: () => void = () => {};
  const firstSave = new Promise<void>((resolve) => (arrived = resolve));
  let count = 0;
  await page.route("**/api/commands", async (route) => {
    const response = await route.fetch();
    if (++count === 1) {
      arrived();
      await held;
    }
    await route.fulfill({ response });
  });
  try {
    await name.fill("First accepted edit");
    await page.keyboard.press("ControlOrMeta+s");
    await firstSave;
    await name.fill("Second edit while saving");
    await page.keyboard.press("ControlOrMeta+s");
    acknowledge();
    await expect(
      page.getByRole("button", { name: "Save changes", exact: true }),
    ).toHaveCount(0);
    await expect
      .poll(
        async () => (await (await request.get("/api/project")).json()).revision,
      )
      .toBe(before.revision + 2);
    const saved = await (await request.get("/api/project")).json();
    expect(saved.features[0].name).toBe("Second edit while saving");
    await expect(name).toHaveValue("Second edit while saving");
  } finally {
    acknowledge();
  }
});
