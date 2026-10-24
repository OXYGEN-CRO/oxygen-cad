import { test, expect, type APIRequestContext } from "@playwright/test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const fixture = JSON.parse(
  readFileSync(
    resolve(process.cwd(), "../examples/electronics-rc.json"),
    "utf8",
  ),
);
async function current(request: APIRequestContext) {
  return (await request.get("/api/project")).json();
}
async function awaitValue(
  request: APIRequestContext,
  find: (project: any) => unknown,
  expected: unknown,
) {
  await expect.poll(async () => find(await current(request))).toEqual(expected);
}
async function runJob(
  request: APIRequestContext,
  operation: string,
  input: object = {},
  actor = "human",
) {
  const p = await current(request);
  const submitted = await request.post("/api/domains/electronics/run", {
    data: { expected_revision: p.revision, actor, operation, input },
  });
  expect(submitted.ok()).toBeTruthy();
  const job = await submitted.json();
  await expect
    .poll(
      async () => {
        const r = await (await request.get(`/api/jobs/${job.id}`)).json();
        return r.state;
      },
      { timeout: 60000 },
    )
    .toMatch(/succeeded|failed|cancelled/);
  return (await request.get(`/api/jobs/${job.id}`)).json();
}

test("electrical source survives human editing, real solve/fabrication, agent lock conflicts and reopening", async ({
  page,
  request,
  baseURL,
}) => {
  test.setTimeout(180000);
  expect(baseURL).not.toMatch(/:8787(?:\/|$)/);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const before = await current(request);
  const seed = await request.put("/api/domains/electronics", {
    data: { expected_revision: before.revision, actor: "human", data: fixture },
  });
  expect(seed.ok()).toBeTruthy();
  await page.goto("/");
  await page
    .getByRole("combobox", { name: "Workspace", exact: true })
    .selectOption("electronics");
  const panel = page.getByRole("region", { name: "Electronics workspace" });
  await expect(
    panel.getByRole("img", { name: "Editable electrical schematic" }),
  ).toBeVisible();
  await panel.locator('[data-component-id="r1"] > rect').click();
  await panel.getByLabel("Value", { exact: true }).fill("2k");
  await panel.getByLabel("Value", { exact: true }).press("Enter");
  await awaitValue(
    request,
    (p) =>
      p.domains.electronics.components.find((c: any) => c.id === "r1").value,
    "2k",
  );
  await panel.getByRole("button", { name: "Run ERC", exact: true }).click();
  await expect(
    panel.getByText("No findings in checked rule set."),
  ).toBeVisible();
  await panel.getByRole("combobox", { name: "Active net" }).selectOption("vin");
  await panel.getByRole("button", { name: "Wire", exact: true }).click();
  await panel.locator('[data-pin-id="v1/p1"] > circle').click();
  await panel.locator('[data-pin-id="r1/p1"] > circle').click();
  await awaitValue(request, (p) => p.domains.electronics.wires.length, 1);
  await panel.getByRole("tab", { name: "Board", exact: true }).click();
  await expect(
    panel.getByRole("img", { name: "Editable PCB layout" }),
  ).toBeVisible();
  await panel.locator('[data-component-id="r1"] > rect').click();
  await panel.getByLabel("X mm", { exact: true }).fill("19");
  await panel.getByLabel("X mm", { exact: true }).press("Enter");
  await awaitValue(
    request,
    (p) =>
      p.domains.electronics.board.placements.find(
        (p: any) => p.component_id === "r1",
      ).at[0],
    19,
  );
  await panel
    .getByRole("button", { name: "Lock footprint", exact: true })
    .click();
  await awaitValue(
    request,
    (p) =>
      p.domains.electronics.board.placements.find(
        (p: any) => p.component_id === "r1",
      ).locked,
    true,
  );
  const locked = await current(request),
    candidate = structuredClone(locked.domains.electronics);
  candidate.board.placements.find((p: any) => p.component_id === "r1").at = [
    20, 11,
  ];
  const refused = await request.put("/api/domains/electronics", {
    data: {
      expected_revision: locked.revision,
      actor: "agent",
      data: candidate,
    },
  });
  expect([409, 422]).toContain(refused.status());
  expect(await refused.text()).toContain("locked");
  const replaced = await request.post("/api/commands", {
    data: {
      expected_revision: locked.revision,
      actor: "agent",
      action: "replace",
      project: {
        ...locked,
        domains: { ...locked.domains, electronics: candidate },
      },
      summary: "Negative lock fixture",
    },
  });
  expect([409, 422]).toContain(replaced.status());
  const forged = await runJob(
    request,
    "edit",
    {
      actor: "human",
      changes: [
        {
          collection: "board.placements",
          action: "upsert",
          id: "pr1",
          value: candidate.board.placements.find((p: any) => p.id === "pr1"),
        },
      ],
    },
    "agent",
  );
  expect(forged.state).toBe("failed");
  expect(forged.message).toContain("locked");
  const stale = await request.put("/api/domains/electronics", {
    data: { expected_revision: before.revision, actor: "human", data: fixture },
  });
  expect(stale.status()).toBe(409);
  const invalid = structuredClone(locked.domains.electronics);
  invalid.board.vias = [
    {
      id: "bad_via",
      net_id: "vin",
      at: [8, 8],
      diameter: 0.6,
      drill: 0.3,
      start_layer: "F.Cu",
      end_layer: "missing",
    },
  ];
  const bad = await request.put("/api/domains/electronics", {
    data: { expected_revision: locked.revision, actor: "human", data: invalid },
  });
  expect(bad.status()).toBe(422);
  await panel.getByRole("tab", { name: "Simulation", exact: true }).click();
  await panel
    .getByRole("button", { name: "Solve circuit", exact: true })
    .click();
  await expect(
    panel.getByRole("img", { name: "Solved circuit probe plot" }),
  ).toBeVisible();
  const deck = await request.get(
    (await panel
      .getByRole("link", { name: "circuit.cir", exact: true })
      .getAttribute("href")) as string,
  );
  expect(await deck.text()).toContain("RR1");
  expect(await deck.text()).toContain("2k");
  await panel.getByRole("tab", { name: "Production", exact: true }).click();
  await panel
    .getByRole("combobox", { name: "Assembly variant" })
    .selectOption("no_cap");
  await panel
    .getByRole("button", { name: "Inspect BOM and placement", exact: true })
    .click();
  await expect(panel.locator("pre")).toContainText("R1");
  await expect(panel.locator("pre")).not.toContainText("C1");
  await panel
    .getByRole("button", { name: "Export fabrication", exact: true })
    .click();
  await expect(
    panel.getByRole("link", { name: "board-Edge_Cuts.gbr", exact: true }),
  ).toBeVisible({ timeout: 60000 });
  for (const [file, role] of [
    ["board-F_Cu.gbr", "Copper,L1,Top"],
    ["board-In1_Cu.gbr", "Copper,L2,Inr"],
    ["board-In2_Cu.gbr", "Copper,L3,Inr"],
    ["board-B_Cu.gbr", "Copper,L4,Bot"],
  ]) {
    const response = await request.get(
      (await panel
        .getByRole("link", { name: file, exact: true })
        .getAttribute("href")) as string,
    );
    expect(response.ok()).toBeTruthy();
    expect(await response.text()).toContain(`%TF.FileFunction,${role}*%`);
  }
  const outline = await request.get(
      (await panel
        .getByRole("link", { name: "board-Edge_Cuts.gbr", exact: true })
        .getAttribute("href")) as string,
    ),
    outlineText = await outline.text();
  expect(outlineText).toContain("%MOMM*%");
  expect(outlineText).toContain("X20000000Y-20000000");
  expect((outlineText.match(/D01\*/g) || []).length).toBe(8);
  const positions = await request.get(
    (await panel
      .getByRole("link", { name: "positions.csv", exact: true })
      .getAttribute("href")) as string,
  );
  expect(await positions.text()).not.toContain("C1");
  await page.reload();
  await page
    .getByRole("combobox", { name: "Workspace", exact: true })
    .selectOption("electronics");
  await panel.locator('[data-component-id="r1"] > rect').click();
  await expect(panel.getByLabel("Value", { exact: true })).toHaveValue("2k");
  await panel.getByRole("tab", { name: "Board", exact: true }).click();
  await panel.locator('[data-component-id="r1"] > rect').click();
  await expect(panel.getByLabel("X mm", { exact: true })).toHaveValue("19");
  await expect(
    panel.getByRole("button", { name: "Unlock footprint", exact: true }),
  ).toBeVisible();
  await panel.getByRole("tab", { name: "Source", exact: true }).click();
  await panel
    .getByRole("button", { name: "Load current source", exact: true })
    .click();
  const later = await current(request),
    manual = structuredClone(later.domains.electronics);
  manual.components.find((c: any) => c.id === "r1").value = "3k";
  const changed = await request.put("/api/domains/electronics", {
    data: { expected_revision: later.revision, actor: "human", data: manual },
  });
  expect(changed.ok()).toBeTruthy();
  await expect(
    panel.getByText(
      `Shared electrical source · revision ${later.revision + 1}`,
      { exact: true },
    ),
  ).toBeVisible();
  await panel
    .getByRole("button", { name: "Apply electrical source", exact: true })
    .click();
  await expect(panel.getByRole("alert")).toContainText(
    "Source revision conflict",
  );
  await awaitValue(
    request,
    (p) =>
      p.domains.electronics.components.find((c: any) => c.id === "r1").value,
    "3k",
  );
  await panel.getByRole("tab", { name: "Board", exact: true }).click();
  await page.screenshot({
    path: test.info().outputPath("electronics-board-dark.png"),
  });
  await page
    .getByRole("button", { name: "Switch to light mode", exact: true })
    .click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await page.screenshot({
    path: test.info().outputPath("electronics-board-light.png"),
  });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
  expect(errors).toEqual([]);
});
