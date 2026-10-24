import { test, expect, type Page } from "@playwright/test";
import { sceneDefault } from "../src/workspaces/visualization/scene";
import { spawnSync } from "node:child_process";
import { writeFileSync } from "node:fs";

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
  const clearColor = () =>
    page.locator(".webgl canvas").evaluate((canvas) => {
      const gl = (canvas as HTMLCanvasElement).getContext("webgl2")!;
      return Array.from(gl.getParameter(gl.COLOR_CLEAR_VALUE) as Float32Array);
    });
  const savedBackground = await clearColor();
  await page.getByRole("button", { name: "Switch to light mode" }).click();
  await page.waitForTimeout(100);
  expect(await clearColor()).toEqual(savedBackground);
  await page.getByRole("button", { name: "Switch to dark mode" }).click();
  await page.waitForTimeout(100);
  expect(await clearColor()).toEqual(savedBackground);
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
  await page.evaluate(
    (detail) =>
      window.dispatchEvent(new CustomEvent("o3d:visualization", { detail })),
    { scene, geometry: { bodies: [] }, assets: {} },
  );
  await expect(page.locator(".webgl")).toHaveAttribute(
    "data-visual-bodies",
    "0",
  );
  expect((await pixels(page)).colored).toBe(0);
  const after = await (await request.get("/api/project")).json();
  expect(after).toEqual(before.project);
  expect(errors).toEqual([]);
});

test("ten and one thousand actual native occurrences retain identity and stay navigable", async ({
  page,
  request,
}, info) => {
  test.skip(
    process.env.O3D_NATIVE_SCENE_TEST !== "1",
    "Requires the canonical native-scene core binary",
  );
  test.setTimeout(120000);
  const before = await (await request.get("/api/project")).json();
  const measurements = [];
  await page.goto("/");
  await expect(page.locator(".webgl[data-field-ready=true]")).toBeVisible();
  for (const count of [10, 1000]) {
    const commands = [
      { command: "component.create", params: { name: "Tile" } },
      {
        command: "solid.box",
        params: { length: 2, width: 2, height: 2, body_name: "Tile" },
      },
      { command: "component.activate", params: { component: 0 } },
    ];
    // Actual native occurrences share one component definition. The returned world
    // geometry and IDs, rather than synthesized meshes, are shown in the browser.
    for (let i = 1; i < count; i++)
      commands.push({
        command: "occurrence.copy",
        params: {
          component: 1,
          translate: [(i % 32) * 4, Math.floor(i / 32) * 4, 0],
        },
      } as any);
    commands.push({ command: "oxygen.scene", params: { mesh: true } } as any);
    const started = performance.now();
    const process = spawnSync(globalThis.process.env.O3D_ENGINE_BINARY!, [], {
      input: commands.map((c) => JSON.stringify(c)).join("\n") + "\n",
      encoding: "utf8",
      maxBuffer: 64 * 1024 * 1024,
      timeout: 60000,
    });
    expect(process.status, process.stderr).toBe(0);
    const replies = process.stdout
      .trim()
      .split("\n")
      .map((line) => JSON.parse(line));
    expect(
      replies.every((r) => r.ok),
      JSON.stringify(replies.find((r) => !r.ok)),
    ).toBe(true);
    const geometry = replies.at(-1).result;
    expect(geometry.bodies).toHaveLength(count);
    expect(new Set(geometry.bodies.map((b: any) => b.id)).size).toBe(count);
    const extractedMs = performance.now() - started;
    const scene = sceneDefault();
    scene.camera.target = count === 10 ? [19, 1, 1] : [63, 61, 1];
    scene.camera.distance = count === 10 ? 60 : 220;
    scene.appearances = {
      "*": {
        color: "#cc4444",
        metallic: 0,
        roughness: 0.8,
        opacity: 1,
        texture: null,
      },
    };
    const previewStart = performance.now();
    await page.evaluate(
      (detail) =>
        window.dispatchEvent(new CustomEvent("o3d:visualization", { detail })),
      { scene, geometry, assets: {} },
    );
    await expect(page.locator(".webgl")).toHaveAttribute(
      "data-visual-bodies",
      String(count),
    );
    const visible = await pixels(page);
    expect(visible.colored).toBeGreaterThan(100);
    const firstFrameMs = performance.now() - previewStart;
    expect(firstFrameMs).toBeLessThan(20000);
    const heap = await page.evaluate(
      () => (performance as any).memory?.usedJSHeapSize ?? null,
    );
    measurements.push({
      occurrences: count,
      nativeExtractionMs: extractedMs,
      firstFrameMs,
      heapBytes: heap,
    });
    scene.isolated = [geometry.bodies[0].id];
    await page.evaluate(
      (detail) =>
        window.dispatchEvent(new CustomEvent("o3d:visualization", { detail })),
      { scene, geometry, assets: {} },
    );
    await page.waitForTimeout(100);
    await page.evaluate(() =>
      window.dispatchEvent(new CustomEvent("o3d:visualization-fit")),
    );
    await page.waitForTimeout(100);
    const rect = await page.locator(".webgl canvas").boundingBox();
    await page.mouse.click(
      rect!.x + rect!.width / 2,
      rect!.y + rect!.height / 2,
    );
    await expect(page.locator(".selection-card code")).toContainText(
      geometry.bodies[0].id + ":face:",
    );
    await page.screenshot({
      path: `test-results/visualization-${count}-native-occurrences.png`,
    });
  }
  const evidencePath = info.outputPath("native-performance.json");
  writeFileSync(evidencePath, JSON.stringify(measurements, null, 2));
  await info.attach("native-occurrence-performance", {
    path: evidencePath,
    contentType: "application/json",
  });
  expect(await (await request.get("/api/project")).json()).toEqual(before);
});
