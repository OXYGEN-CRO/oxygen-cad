import { test, expect } from "@playwright/test";
import { sceneDefault } from "../src/workspaces/visualization/scene";

test("large indexed mesh preserves all triangles with bounded draw calls and native face picking", async ({
  page,
  request,
}) => {
  test.setTimeout(120000);
  const before = await (await request.get("/api/project")).json();
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/");
  await expect(page.locator(".webgl[data-field-ready=true]")).toBeVisible();
  const scene = sceneDefault();
  scene.camera = {
    target: [0, 0, 5],
    yaw: 0,
    pitch: Math.PI / 2,
    roll: 0,
    distance: 90,
    fov: 0,
  };
  scene.style = "shaded_edges";
  scene.appearances = {
    "large-mesh": {
      color: "#dd3322",
      metallic: 0,
      roughness: 0.6,
      opacity: 1,
      texture: null,
    },
  };
  const positions: number[][] = [],
    triangles: number[][] = [],
    tri_face: number[] = [];
  const nx = 250,
    ny = 200;
  for (let y = 0; y <= ny; y++)
    for (let x = 0; x <= nx; x++)
      positions.push([-30 + (60 * x) / nx, -20 + (40 * y) / ny, 5]);
  for (let y = 0; y < ny; y++)
    for (let x = 0; x < nx; x++) {
      const a = y * (nx + 1) + x,
        b = a + 1,
        c = a + nx + 1,
        d = c + 1;
      triangles.push([a, b, d], [a, d, c]);
      tri_face.push(tri_face.length, tri_face.length + 1);
    }
  const geometry = {
    bodies: [
      {
        id: "large-mesh",
        name: "Real tessellated test panel",
        geometry_kind: "mesh",
        visible: true,
        mesh: { positions, triangles, tri_face },
      },
    ],
  };
  await page.evaluate(
    (detail) =>
      window.dispatchEvent(new CustomEvent("o3d:visualization", { detail })),
    { scene, geometry, assets: {} },
  );
  await expect(page.locator(".webgl")).toHaveAttribute(
    "data-visual-triangles",
    "100000",
  );
  await expect(page.locator(".webgl")).toHaveAttribute(
    "data-draw-objects",
    "2",
  );
  await expect
    .poll(async () =>
      Number(await page.locator(".webgl").getAttribute("data-render-calls")),
    )
    .toBeLessThan(10);
  const rect = (await page.locator(".webgl canvas").boundingBox())!;
  await page.mouse.click(
    rect.x + rect.width * 0.503,
    rect.y + rect.height * 0.487,
  );
  await expect(page.locator(".selection-card code")).toContainText(
    "large-mesh:face:",
  );
  await expect
    .poll(
      async () =>
        (await (await request.get("/api/state")).json()).context.selection
          ?.id ?? "",
    )
    .toMatch(/^large-mesh:face:\d+$/);
  const selected = (await (await request.get("/api/state")).json()).context
    .selection;
  expect(selected.face.area_mm2).toBeCloseTo(0.024, 6);
  expect(selected.face.normal).toEqual([0, 0, 1]);
  const faceId = Number(selected.id.split(":").at(-1));
  expect(faceId).toBeGreaterThanOrEqual(0);
  expect(faceId).toBeLessThan(triangles.length);
  const bounds = triangles[faceId].map((i) => positions[i]);
  expect(selected.point[0]).toBeGreaterThanOrEqual(
    Math.min(...bounds.map((p) => p[0])) - 1e-5,
  );
  expect(selected.point[0]).toBeLessThanOrEqual(
    Math.max(...bounds.map((p) => p[0])) + 1e-5,
  );
  await expect
    .poll(async () =>
      Number(await page.locator(".webgl").getAttribute("data-draw-objects")),
    )
    .toBeLessThanOrEqual(4);
  scene.hidden = ["large-mesh"];
  await page.evaluate(
    (detail) =>
      window.dispatchEvent(new CustomEvent("o3d:visualization", { detail })),
    { scene, geometry, assets: {} },
  );
  await expect(page.locator(".webgl")).toHaveAttribute(
    "data-draw-objects",
    "0",
  );
  expect(errors).toEqual([]);
  expect(await (await request.get("/api/project")).json()).toEqual(before);
});

test("slow status responses do not accumulate overlapping background polls", async ({
  page,
}) => {
  let active = 0,
    maximum = 0,
    completed = 0;
  await page.route("**/api/state", async (route) => {
    active++;
    maximum = Math.max(maximum, active);
    try {
      await new Promise((resolve) => setTimeout(resolve, 1800));
      const response = await route.fetch();
      await route.fulfill({ response });
      completed++;
    } finally {
      active--;
    }
  });
  await page.goto("/");
  await expect(page.locator(".webgl[data-field-ready=true]")).toBeVisible();
  await expect
    .poll(() => completed, { timeout: 10000 })
    .toBeGreaterThanOrEqual(2);
  expect(maximum).toBe(1);
});
