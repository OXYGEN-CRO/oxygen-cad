import {
  test,
  expect,
  type APIRequestContext,
  type Page,
} from "@playwright/test";

async function inspect(request: APIRequestContext) {
  const response = await request.get("/api/native/inspect");
  expect(response.ok(), await response.text()).toBeTruthy();
  return response.json();
}
async function native(
  request: APIRequestContext,
  command: string,
  params: object = {},
  actor = "agent",
) {
  const project = await (await request.get("/api/project")).json();
  const response = await request.post("/api/native/execute", {
    data: { expected_revision: project.revision, actor, command, params },
  });
  expect(response.ok(), await response.text()).toBeTruthy();
  return response.json();
}
async function idle(page: Page) {
  await expect(page.getByLabel("Modeling workspace")).toHaveAttribute(
    "aria-busy",
    "false",
  );
}
async function clickAt(page: Page, x: number, y: number) {
  const position = await page
    .getByRole("img", { name: /Sketch canvas/ })
    .evaluate(
      (element, coordinates) => {
        const svg = element as SVGSVGElement;
        const p = svg.createSVGPoint();
        p.x = coordinates[0];
        p.y = -coordinates[1];
        const result = p.matrixTransform(svg.getScreenCTM()!);
        return { x: result.x, y: result.y };
      },
      [x, y],
    );
  await page.mouse.click(position.x, position.y);
}
test.beforeEach(async ({ request }) => {
  const project = await (await request.get("/api/project")).json();
  const response = await request.post("/api/commands", {
    data: {
      expected_revision: project.revision,
      actor: "human",
      action: "replace",
      project: {
        ...project,
        features: [],
        native_document: null,
        domains: {},
        parameters: {},
      },
      summary: "Reset disposable modeling fixture",
    },
  });
  expect(response.ok(), await response.text()).toBeTruthy();
});

test("draw, constrain, reject a conflicting dimension, extrude and cold reopen", async ({
  page,
  request,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/tests/modeling/fixture.html");
  await page.getByLabel("Sketch name", { exact: true }).fill("Plate sketch");
  await page
    .getByRole("button", { name: "Create sketch", exact: true })
    .click();
  await idle(page);
  await page
    .getByLabel("Sketch tool", { exact: true })
    .selectOption("rectangle");
  await clickAt(page, -20, -15);
  await clickAt(page, 20, 15);
  await idle(page);
  await expect(page.locator(".modeling-curve")).toHaveCount(4);
  await page.getByLabel("Sketch tool", { exact: true }).selectOption("circle");
  await clickAt(page, 0, 0);
  await clickAt(page, 5, 0);
  await idle(page);
  await expect(page.locator(".modeling-curve")).toHaveCount(5);
  await page.getByText("Entities and dimensions", { exact: true }).click();
  await page.getByRole("button", { name: "l1 · line", exact: true }).click();
  await idle(page);
  await page
    .getByLabel("Dimension type", { exact: true })
    .selectOption("length");
  await page
    .getByLabel("Dimension value", { exact: true })
    .fill("width = 40 mm");
  await page
    .getByRole("button", { name: "Add dimension", exact: true })
    .click();
  await idle(page);
  await page
    .getByRole("button", { name: "Auto constrain", exact: true })
    .click();
  await idle(page);
  await expect(page.getByRole("status")).toHaveText(
    "Fully constrained · 0 DOF",
  );
  await page
    .getByRole("button", { name: "Show constraint glyphs", exact: true })
    .click();
  await idle(page);
  await page.screenshot({
    path: "test-results/modeling-sketch-dark.png",
    fullPage: true,
  });
  const constrained = await inspect(request);
  await page.getByLabel("Dimension value", { exact: true }).fill("41 mm");
  await page
    .getByRole("button", { name: "Add dimension", exact: true })
    .click();
  await idle(page);
  await expect(page.getByRole("alert")).toContainText(
    /determined|already|conflict/,
  );
  const failed = await inspect(request);
  expect(failed.revision).toBe(constrained.revision);
  expect(failed.sketches[0].curves).toEqual(constrained.sketches[0].curves);
  await page.getByRole("button", { name: "Dismiss", exact: true }).click();
  await page
    .getByRole("button", { name: "Finish sketch", exact: true })
    .click();
  await idle(page);
  await page
    .getByRole("button", { name: "Extrude selected profiles", exact: true })
    .click();
  await page.getByLabel("Distance", { exact: true }).fill("5 mm");
  await page
    .getByRole("button", { name: "Apply extrude", exact: true })
    .click();
  await idle(page);
  const model = await inspect(request);
  expect(model.inspection.bodies).toHaveLength(1);
  expect(model.inspection.bodies[0].volume_mm3).toBeCloseTo(
    (1200 - Math.PI * 25) * 5,
    0,
  );
  await native(request, "parameters.change", {
    name: "width",
    expression: "50 mm",
  });
  await page.reload();
  await page.getByRole("button", { name: "Parameters", exact: true }).click();
  await expect(
    page.getByLabel("Expression width", { exact: true }),
  ).toHaveValue("50 mm");
  const reopened = await inspect(request);
  expect(reopened.sketches[0].dof).toBe(0);
  expect(reopened.inspection.bodies[0].volume_mm3).toBeGreaterThan(
    model.inspection.bodies[0].volume_mm3,
  );
  expect(errors).toEqual([]);
});

test("individual nested profiles feed two editable features from the same sketch", async ({
  page,
  request,
}) => {
  await native(request, "sketch.create", {
    plane: "XY",
    name: "Reusable regions",
  });
  await native(request, "sketch.rectangle.two_point", {
    p0: [-20, -15],
    p1: [20, 15],
  });
  await native(request, "sketch.circle.center", { center: [0, 0], radius: 5 });
  await native(request, "sketch.finish");
  await page.goto("/tests/modeling/fixture.html");
  await page.getByRole("checkbox", { name: /Profile 0 ·/ }).check();
  await idle(page);
  await page
    .getByRole("button", { name: "Extrude selected profiles", exact: true })
    .click();
  await expect(
    page.getByLabel("Profiles (stable curve loops or indices)", {
      exact: true,
    }),
  ).toHaveValue("[0]");
  await page.getByLabel("Distance", { exact: true }).fill("5 mm");
  await page
    .getByRole("button", { name: "Apply extrude", exact: true })
    .click();
  await idle(page);
  const plate = await inspect(request);
  expect(plate.inspection.bodies).toHaveLength(1);
  expect(plate.inspection.bodies[0].volume_mm3).toBeCloseTo(
    (1200 - Math.PI * 25) * 5,
    0,
  );
  await page.getByRole("button", { name: "Sketch", exact: true }).click();
  await page.getByRole("checkbox", { name: /Profile 0 ·/ }).uncheck();
  await idle(page);
  await page.getByRole("checkbox", { name: /Profile 1 ·/ }).check();
  await idle(page);
  await page
    .getByRole("button", { name: "Extrude selected profiles", exact: true })
    .click();
  await expect(
    page.getByLabel("Profiles (stable curve loops or indices)", {
      exact: true,
    }),
  ).toHaveValue("[1]");
  await page.getByLabel("Distance", { exact: true }).fill("10 mm");
  await page
    .getByRole("button", { name: "Apply extrude", exact: true })
    .click();
  await idle(page);
  const two = await inspect(request);
  expect(two.inspection.bodies).toHaveLength(2);
  expect(
    two.inspection.bodies.find((b: any) => b.volume_mm3 < 1000).volume_mm3,
  ).toBeCloseTo(Math.PI * 25 * 10, 0);
  await page.reload();
  const reopened = await inspect(request);
  expect(reopened.sketches).toHaveLength(1);
  expect(
    reopened.inspection.timeline.filter(
      (f: any) => f.type === "ExtrudeFeature",
    ),
  ).toHaveLength(2);
  expect(reopened.inspection.bodies).toHaveLength(2);
});

test("twenty alternating GUI and agent parameter edits preserve equations and revision conflicts", async ({
  page,
  request,
}) => {
  await native(request, "parameters.add", {
    name: "height",
    expression: "10 mm",
    unit: "mm",
  });
  await native(request, "parameters.add", {
    name: "width",
    expression: "2 * height + 2 mm",
    unit: "mm",
  });
  await native(request, "solid.box", {
    length: "width",
    width: "10 mm",
    height: "height",
    body_name: "Parametric",
  });
  await page.goto("/tests/modeling/fixture.html");
  await page.getByRole("button", { name: "Parameters", exact: true }).click();
  for (let i = 0; i < 10; i++) {
    const row = page
      .locator(".modeling-parameter")
      .filter({ has: page.getByLabel("Expression height", { exact: true }) });
    await page
      .getByLabel("Expression height", { exact: true })
      .fill(`${11 + i * 2} mm`);
    await row
      .getByRole("button", { name: "Update parameter", exact: true })
      .click();
    await idle(page);
    const afterHuman = await inspect(request);
    expect(
      afterHuman.inspection.params.find((p: any) => p.name === "height").value,
    ).toBe(11 + i * 2);
    await native(request, "parameters.change", {
      name: "height",
      expression: `${12 + i * 2} mm`,
    });
    await page.reload();
    await page.getByRole("button", { name: "Parameters", exact: true }).click();
    await expect(
      page.getByLabel("Expression height", { exact: true }),
    ).toHaveValue(`${12 + i * 2} mm`);
  }
  const current = await inspect(request);
  expect(
    current.inspection.params.find((p: any) => p.name === "width").value,
  ).toBe(62);
  expect(current.inspection.bodies[0].volume_mm3).toBeCloseTo(62 * 10 * 30, 4);
  const stale = await request.post("/api/native/execute", {
    data: {
      expected_revision: current.revision - 1,
      actor: "agent",
      command: "parameters.change",
      params: { name: "height", expression: "100 mm" },
    },
  });
  expect(stale.status()).toBe(409);
  expect((await inspect(request)).revision).toBe(current.revision);
});

test("trim path preview uses the engine and does not mutate until applied", async ({
  page,
  request,
}) => {
  await native(request, "sketch.create", { plane: "XY", name: "Trim" });
  await native(request, "sketch.line", {
    points: [
      [-20, 0],
      [20, 0],
    ],
    ids: ["horizontal"],
    infer: false,
  });
  await native(request, "sketch.line", {
    points: [
      [-5, -10],
      [-5, 10],
    ],
    ids: ["left"],
    infer: false,
  });
  await native(request, "sketch.line", {
    points: [
      [5, -10],
      [5, 10],
    ],
    ids: ["right"],
    infer: false,
  });
  const before = await inspect(request);
  await page.goto("/tests/modeling/fixture.html");
  await page
    .getByLabel("Sketch tool", { exact: true })
    .selectOption("trim_path");
  const positions = await page
    .getByRole("img", { name: "Sketch canvas Trim" })
    .evaluate((element) => {
      const svg = element as SVGSVGElement;
      return [
        [0, -2],
        [0, 2],
      ].map(([x, y]) => {
        const p = svg.createSVGPoint();
        p.x = x;
        p.y = -y;
        const q = p.matrixTransform(svg.getScreenCTM()!);
        return { x: q.x, y: q.y };
      });
    });
  await page.mouse.move(positions[0].x, positions[0].y);
  await page.mouse.down();
  await page.mouse.move(positions[1].x, positions[1].y, { steps: 12 });
  await page.mouse.up();
  await idle(page);
  await expect(
    page.getByText("Engine preview · 1 removed pieces", { exact: true }),
  ).toBeVisible();
  const preview = await inspect(request);
  expect(preview.revision).toBe(before.revision);
  expect(preview.sketches[0].curves).toEqual(before.sketches[0].curves);
  await expect(page.locator(".modeling-trim-preview")).toHaveCount(1);
  await page
    .getByRole("button", { name: "Apply trim path", exact: true })
    .click();
  await idle(page);
  const after = await inspect(request);
  expect(after.revision).toBeGreaterThan(before.revision);
  expect(after.sketches[0].curves).toHaveLength(4);
  expect(
    after.sketches[0].curves.some(
      (c: any) => c.start_at[0] < 0 && c.end_at[0] > 0,
    ),
  ).toBeFalsy();
});

test("reference image calibration and bundled text survive reopening", async ({
  page,
  request,
}) => {
  await native(request, "sketch.create", {
    plane: "XY",
    name: "Image and text",
  });
  await page.goto("/tests/modeling/fixture.html");
  await page
    .getByText("Reference image and calibration", { exact: true })
    .click();
  await page.getByLabel("Insert PNG or JPEG", { exact: true }).setInputFiles({
    name: "reference.png",
    mimeType: "image/png",
    buffer: Buffer.from(
      "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=",
      "base64",
    ),
  });
  await idle(page);
  await expect(page.locator(".modeling-sketch-canvas image")).toHaveCount(1);
  const before = await inspect(request);
  await page
    .getByLabel("Reference image", { exact: true })
    .selectOption(String(before.document.canvases[0].id));
  await page.getByLabel("True distance (mm)", { exact: true }).fill("20");
  await page
    .getByRole("button", { name: "Calibrate reference image", exact: true })
    .click();
  await idle(page);
  expect((await inspect(request)).document.canvases[0].width).toBe(200);
  await page
    .getByText("Sketch modifications and 3D paths", { exact: true })
    .click();
  await page
    .getByLabel("Modify sketch", { exact: true })
    .selectOption("sketch.text");
  await page.getByLabel("Text", { exact: true }).fill("HI");
  await page
    .getByRole("button", { name: "Apply sketch text", exact: true })
    .click();
  await idle(page);
  await expect(page.locator(".modeling-curve")).not.toHaveCount(0);
  const written = await inspect(request);
  expect(written.sketches[0].curves.length).toBeGreaterThan(0);
  await page.reload();
  await expect(page.locator(".modeling-sketch-canvas image")).toHaveCount(1);
  const reopened = await inspect(request);
  expect(reopened.document.canvases).toEqual(written.document.canvases);
  expect(reopened.sketches[0].curves).toEqual(written.sketches[0].curves);
});

test("plastic operation dialogs create measured editable enclosure features", async ({
  page,
  request,
}) => {
  await native(request, "solid.box", {
    length: 40,
    width: 30,
    height: 2,
    body_name: "Plate",
  });
  await page.goto("/tests/modeling/fixture.html");
  await page.getByRole("button", { name: "Solid", exact: true }).click();
  await page
    .getByLabel("Solid operation", { exact: true })
    .selectOption("plastic.boss");
  await page.getByLabel("Body", { exact: true }).fill("Plate");
  await page
    .getByRole("button", { name: "Apply plastic boss", exact: true })
    .click();
  await idle(page);
  const boss = await inspect(request);
  expect(boss.inspection.bodies[0].volume_mm3).toBeCloseTo(
    2400 + Math.PI * (16 - 2.25) * 8,
    0,
  );
  await page
    .getByLabel("Solid operation", { exact: true })
    .selectOption("plastic.rest");
  await page.getByLabel("Body", { exact: true }).fill("Plate");
  await page
    .getByRole("button", { name: "Apply plastic rest", exact: true })
    .click();
  await idle(page);
  const rest = await inspect(request);
  expect(
    rest.inspection.bodies[0].volume_mm3 - boss.inspection.bodies[0].volume_mm3,
  ).toBeCloseTo(Math.PI * 9 * 3, 0);
  await page.reload();
  await page.getByRole("button", { name: "History", exact: true }).click();
  await expect(page.getByRole("button", { name: /Boss1/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /Rest1/ })).toBeVisible();
  expect(
    (await inspect(request)).inspection.timeline.every((f: any) => !f.error),
  ).toBeTruthy();
});

test("human unlock and lock preserve the native editing context and protect agent edits", async ({
  page,
  request,
}) => {
  const empty = await (await request.get("/api/project")).json();
  const seeded = await request.post("/api/commands", {
    data: {
      expected_revision: empty.revision,
      actor: "human",
      action: "replace",
      project: {
        ...empty,
        parameters: { width: { value: 10, unit: "mm", locked: true } },
      },
      summary: "Seed a migrated user lock",
    },
  });
  expect(seeded.ok(), await seeded.text()).toBeTruthy();
  await native(request, "sketch.create", {
    plane: "XY",
    name: "Locked sketch",
  });
  await native(request, "sketch.line", {
    points: [
      [0, 0],
      [10, 0],
    ],
  });
  const before = await (await request.get("/api/project")).json();
  await page.goto("/tests/modeling/fixture.html");
  await page.getByRole("button", { name: "Parameters", exact: true }).click();
  await page
    .getByRole("button", { name: "Unlock parameter width", exact: true })
    .click();
  await idle(page);
  await expect(
    page.getByRole("button", { name: "Lock parameter width", exact: true }),
  ).toBeVisible();
  const unlocked = await (await request.get("/api/project")).json();
  expect(unlocked.parameters.width.locked).toBeFalsy();
  expect(unlocked.domains.modeling.parameter_locks.width).toBeFalsy();
  expect(unlocked.domains.modeling.native_context).toEqual(
    before.domains.modeling.native_context,
  );
  expect(unlocked.domains.modeling.sketch_entry).toEqual(
    before.domains.modeling.sketch_entry,
  );
  expect(unlocked.native_document).toEqual(before.native_document);
  await native(request, "parameters.change", {
    name: "width",
    expression: "20 mm",
  });
  await page.reload();
  await page.getByRole("button", { name: "Parameters", exact: true }).click();
  await page
    .getByRole("button", { name: "Lock parameter width", exact: true })
    .click();
  await idle(page);
  const locked = await (await request.get("/api/project")).json();
  expect(locked.parameters.width.locked).toBeTruthy();
  expect(locked.domains.modeling.parameter_locks.width).toBeTruthy();
  const refused = await request.post("/api/native/execute", {
    data: {
      expected_revision: locked.revision,
      actor: "agent",
      command: "parameters.change",
      params: { name: "width", expression: "30 mm" },
    },
  });
  expect(refused.status()).toBe(409);
  expect((await refused.json()).detail).toMatch(/locked|lock/i);
  expect((await (await request.get("/api/project")).json()).revision).toBe(
    locked.revision,
  );
  await page.reload();
  await page.getByRole("button", { name: "Parameters", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Unlock parameter width", exact: true }),
  ).toBeVisible();
});

test("native history and topology work in both themes at narrow widths", async ({
  page,
  request,
}) => {
  const created = await native(request, "solid.box", {
    length: 40,
    width: 30,
    height: 10,
    body_name: "Body",
  });
  await page.goto("/tests/modeling/fixture.html");
  await page.getByLabel("Modeling workspace").waitFor();
  await page.evaluate(
    (feature) =>
      window.dispatchEvent(
        new CustomEvent("o3d:modeling", {
          detail: { feature, tab: "history" },
        }),
      ),
    created.result.feature,
  );
  await expect(
    page.getByLabel("Feature height", { exact: true }),
  ).toBeVisible();
  await idle(page);
  await page.getByLabel("Feature height", { exact: true }).fill("12 mm");
  await page
    .getByRole("button", { name: "Save feature definition", exact: true })
    .click();
  await idle(page);
  expect((await inspect(request)).inspection.bodies[0].volume_mm3).toBeCloseTo(
    14400,
    4,
  );
  await page
    .getByRole("button", { name: "Suppress / unsuppress", exact: true })
    .click();
  await idle(page);
  expect((await inspect(request)).inspection.bodies).toHaveLength(0);
  await page
    .getByRole("button", { name: "Suppress / unsuppress", exact: true })
    .click();
  await idle(page);
  await page.getByRole("button", { name: "Solid", exact: true }).click();
  await page
    .getByText("Native bodies, faces and edges", { exact: true })
    .click();
  await page
    .getByRole("button", { name: "List topology", exact: true })
    .click();
  await idle(page);
  await expect(
    page.locator(".modeling-topology-list input[type=checkbox]"),
  ).toHaveCount(6);
  await page
    .locator(".modeling-topology-list input[type=checkbox]")
    .first()
    .check();
  await idle(page);
  expect((await inspect(request)).inspection.selection[0].type).toBe("face");
  for (const width of [320, 440, 1024]) {
    await page.setViewportSize({ width, height: 1000 });
    for (const theme of ["light", "dark"]) {
      await page.evaluate((t) => {
        document.documentElement.dataset.theme = t;
      }, theme);
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      ).toBeTruthy();
      const colors = await page
        .getByLabel("Solid operation", { exact: true })
        .evaluate((element) => ({
          color: getComputedStyle(element).color,
          background: getComputedStyle(element).backgroundColor,
        }));
      expect(colors.color).not.toBe(colors.background);
    }
  }
  expect(created.result.feature).toBeGreaterThan(0);
});
