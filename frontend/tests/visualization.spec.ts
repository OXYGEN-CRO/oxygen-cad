import { test, expect, type Page } from "@playwright/test";
import { sceneDefault } from "../src/workspaces/visualization/scene";

async function pixels(page: Page) {
  return page.evaluate(
    () =>
      new Promise<{ sum: number; colored: number }>((resolve) =>
        requestAnimationFrame(() => {
          const canvas = document.querySelector(
            ".webgl canvas",
          ) as HTMLCanvasElement;
          const gl = canvas.getContext("webgl2")!;
          const bytes = new Uint8Array(
            gl.drawingBufferWidth * gl.drawingBufferHeight * 4,
          );
          gl.readPixels(
            0,
            0,
            gl.drawingBufferWidth,
            gl.drawingBufferHeight,
            gl.RGBA,
            gl.UNSIGNED_BYTE,
            bytes,
          );
          let sum = 0,
            colored = 0;
          for (let i = 0; i < bytes.length; i += 4) {
            sum =
              (sum + bytes[i] * 3 + bytes[i + 1] * 5 + bytes[i + 2] * 7) >>> 0;
            if (Math.abs(bytes[i] - bytes[i + 2]) > 20) colored++;
          }
          resolve({ sum, colored });
        }),
      ),
  );
}

test("real native geometry responds to scene camera, material, section and explosion without editing the project", async ({
  page,
  request,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const before = await (await request.get("/api/state")).json();
  await page.goto("/");
  await expect(page.locator(".webgl[data-field-ready=true]")).toBeVisible();
  const scene = sceneDefault();
  const bounds = before.build.bounds as number[][];
  scene.camera.target = bounds[0].map(
    (n: number, i: number) => (n + bounds[1][i]) / 2,
  );
  scene.camera.distance =
    Math.hypot(...bounds[0].map((n: number, i: number) => bounds[1][i] - n)) *
    2;
  scene.appearances = {
    "*": {
      color: "#ee2222",
      metallic: 0,
      roughness: 0.8,
      opacity: 1,
      texture: null,
    },
  };
  const geometry = {
    bodies: [
      {
        id: "part",
        name: "Native product",
        visible: true,
        mesh: { faces: before.build.faces },
      },
    ],
  };
  const preview = async () => {
    await page.evaluate(
      (detail) =>
        window.dispatchEvent(new CustomEvent("o3d:visualization", { detail })),
      { scene, geometry, assets: {} },
    );
    await page.waitForTimeout(100);
  };
  await preview();
  const painted = await pixels(page);
  expect(painted.colored).toBeGreaterThan(1000);
  scene.camera.fov = Math.PI / 4;
  await preview();
  await expect(page.locator(".webgl")).toHaveAttribute(
    "data-projection",
    "perspective",
  );
  const perspective = await pixels(page);
  expect(perspective.sum).not.toBe(painted.sum);
  scene.sections = [
    {
      name: "Mid-plane",
      normal: [1, 0, 0],
      offset_mm: scene.camera.target[0],
      enabled: true,
    },
  ];
  await preview();
  const section = await pixels(page);
  expect(section.colored).toBeLessThan(perspective.colored * 0.85);
  scene.sections = [];
  scene.transforms = { part: [30, 0, 0] };
  await preview();
  const exploded = await pixels(page);
  expect(exploded.sum).not.toBe(perspective.sum);
  scene.transforms = {};
  scene.hidden = ["part"];
  await preview();
  const hidden = await pixels(page);
  expect(hidden.colored).toBe(0);
  scene.hidden = [];
  scene.style = "wireframe";
  await preview();
  await expect(page.locator(".webgl")).toHaveAttribute(
    "data-visual-style",
    "wireframe",
  );
  const wire = await pixels(page);
  expect(wire.colored).toBeLessThan(perspective.colored);
  scene.style = "shaded";
  await preview();
  const canvas = page.locator(".webgl canvas");
  const rect = await canvas.boundingBox();
  await page.mouse.move(rect!.x + rect!.width / 2, rect!.y + rect!.height / 2);
  await page.mouse.down();
  await page.mouse.move(
    rect!.x + rect!.width / 2 + 60,
    rect!.y + rect!.height / 2 + 30,
    { steps: 8 },
  );
  await page.mouse.up();
  const orbit = await pixels(page);
  expect(orbit.sum).not.toBe(perspective.sum);
  const capture = await page.evaluate(() => {
    let snapshot: unknown;
    window.addEventListener(
      "o3d:visualization-camera",
      ((event: CustomEvent) => (snapshot = event.detail)) as EventListener,
      { once: true },
    );
    window.dispatchEvent(new CustomEvent("o3d:visualization-capture"));
    return snapshot;
  });
  expect((capture as any).fov).toBeCloseTo(Math.PI / 4);
  await page.screenshot({
    path: "test-results/visualization-native-scene.png",
  });
  const after = await (await request.get("/api/project")).json();
  expect(after).toEqual(before.project);
  expect(errors).toEqual([]);
});
