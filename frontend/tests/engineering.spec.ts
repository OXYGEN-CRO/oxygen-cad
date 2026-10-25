import { test, expect, type Page } from "@playwright/test";

async function openEngineering(page: Page) {
  await page
    .getByRole("button", { name: "Project tools", exact: true })
    .click();
  await page
    .getByRole("menuitem", { name: "Engineering workflow", exact: true })
    .click();
  await expect(
    page.getByRole("region", { name: "Engineering workflow" }),
  ).toBeVisible();
}

test("engineering brief saves, reopens, evaluates and clears old evidence on draft edits", async ({
  page,
  request,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const initial = await (await request.get("/api/project")).json();
  const requirement = initial.requirements.find(
    (value: { enabled: boolean; kind: string }) =>
      value.enabled && value.kind === "max_dimension",
  );
  expect(requirement).toBeDefined();
  await page.goto("/");
  await openEngineering(page);
  await page
    .getByRole("button", { name: "Create engineering brief", exact: true })
    .click();
  await page
    .getByLabel("Task and intended use", { exact: true })
    .fill("Carry a supplied camera with an explicitly reviewed mount");
  await page
    .getByRole("button", { name: "Link existing requirement", exact: true })
    .click();
  await page
    .getByLabel("Requirement link_1", { exact: true })
    .selectOption(requirement.id);
  await expect(
    page.getByRole("button", { name: "Evaluate traceability", exact: true }),
  ).toBeDisabled();
  await page.getByRole("button", { name: "Save changes", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Evaluate traceability", exact: true }),
  ).toBeEnabled();
  const saved = await (await request.get("/api/project")).json();
  expect(saved.engineering_brief.task).toContain("supplied camera");
  expect(saved.engineering_brief.links[0].requirement_id).toBe(requirement.id);
  await page.reload();
  await openEngineering(page);
  await expect(
    page.getByLabel("Task and intended use", { exact: true }),
  ).toHaveValue(saved.engineering_brief.task);
  await expect(
    page.getByLabel("Requirement link_1", { exact: true }),
  ).toHaveValue(requirement.id);
  const checkResponse = await request.post("/api/jobs/check", { data: {} });
  expect(checkResponse.ok()).toBeTruthy();
  const job = await checkResponse.json();
  await expect
    .poll(
      async () =>
        (await (await request.get(`/api/jobs/${job.id}`)).json()).state,
      { timeout: 30000 },
    )
    .toBe("succeeded");
  const checked = await (await request.get(`/api/jobs/${job.id}`)).json();
  expect(checked.result.state).toBe("passed");
  expect(
    checked.result.findings.find(
      (finding: { feature: string }) => finding.feature === requirement.id,
    ),
  ).toMatchObject({
    code: requirement.kind,
    severity: "info",
    limit: requirement.value,
  });
  await page
    .getByRole("button", { name: "Evaluate traceability", exact: true })
    .click();
  await expect(
    page.getByRole("status").filter({ hasText: "Traceability:" }),
  ).toContainText("Traceability: unknown");
  const bindingResponse = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === "/api/engineering/brief/bind" &&
      response.request().method() === "POST",
  );
  await page
    .getByRole("button", { name: "Bind current evidence", exact: true })
    .click();
  const response = await bindingResponse;
  expect(response.ok()).toBeTruthy();
  const bound = await response.json();
  expect(bound.project.revision).toBe(saved.revision + 1);
  expect(bound.traceability).toMatchObject({
    revision: bound.project.revision,
    state: "passed",
    links: [{ id: "link_1", requirement_id: requirement.id, status: "passed" }],
  });
  const traceability = page
    .getByRole("status")
    .filter({ hasText: "Traceability:" });
  await expect(traceability).toHaveText(
    `Traceability: passed · project revision ${bound.project.revision}`,
  );
  // Let a real studio refresh process the new revision. Its React effects must
  // preserve the report returned through the queued binding mutation.
  await page.waitForResponse(
    (refresh) =>
      new URL(refresh.url()).pathname === "/api/state" &&
      refresh.request().method() === "GET",
  );
  await expect(traceability).toHaveText(
    `Traceability: passed · project revision ${bound.project.revision}`,
  );
  const persisted = await (await request.get("/api/project")).json();
  expect(persisted.revision).toBe(bound.project.revision);
  expect(persisted.engineering_brief.links[0].evidence.source_sha256).toBe(
    bound.traceability.links[0].evidence.source_sha256,
  );
  expect(
    await (await request.get("/api/engineering/brief")).json(),
  ).toMatchObject({ revision: bound.project.revision, state: "passed" });
  await page.reload();
  await openEngineering(page);
  await page
    .getByRole("button", { name: "Evaluate traceability", exact: true })
    .click();
  await expect(traceability).toHaveText(
    `Traceability: passed · project revision ${bound.project.revision}`,
  );
  await page
    .getByLabel("Task and intended use", { exact: true })
    .fill("Changed intended use requires review");
  await expect(
    page.getByRole("status").filter({ hasText: "Traceability:" }),
  ).toHaveCount(0);
  // Restore the explicit test draft through normal save so other workflows use a current project.
  await page.getByRole("button", { name: "Save changes", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Evaluate traceability", exact: true }),
  ).toBeEnabled();
  expect(errors).toEqual([]);
});

test("loading and saving a brief example preserves existing joint requirements and components exactly", async ({
  page,
  request,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const current = await (await request.get("/api/project")).json();
  const exampleResponse = await request.get("/api/joints/example");
  expect(exampleResponse.ok()).toBeTruthy();
  const jointExample = await exampleResponse.json();
  const configureResponse = await request.post("/api/joints/configure", {
    data: {
      ...jointExample,
      expected_revision: current.revision,
      actor: "human",
    },
  });
  expect(configureResponse.ok()).toBeTruthy();
  const configured = (await configureResponse.json()).project;
  expect(
    configured.engineering_brief.joint_requirements.length,
  ).toBeGreaterThan(0);
  expect(configured.engineering_brief.components.length).toBeGreaterThan(0);
  const briefExample = await (
    await request.get("/api/engineering/examples/brief")
  ).json();
  await page.goto("/");
  await openEngineering(page);
  await page.getByRole("button", { name: "Load example", exact: true }).click();
  await expect(
    page.getByLabel("Task and intended use", { exact: true }),
  ).toHaveValue(briefExample.task);
  await page.getByRole("button", { name: "Save changes", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Save changes", exact: true }),
  ).toHaveCount(0);
  const saved = await (await request.get("/api/project")).json();
  expect(saved.revision).toBe(configured.revision + 1);
  expect(saved.engineering_brief.task).toBe(briefExample.task);
  expect(saved.engineering_brief.joint_requirements).toEqual(
    configured.engineering_brief.joint_requirements,
  );
  expect(saved.engineering_brief.components).toEqual(
    configured.engineering_brief.components,
  );
  await page.reload();
  await openEngineering(page);
  await expect(
    page.getByLabel("Task and intended use", { exact: true }),
  ).toHaveValue(briefExample.task);
  const reopened = await (await request.get("/api/project")).json();
  expect(reopened.engineering_brief.joint_requirements).toEqual(
    configured.engineering_brief.joint_requirements,
  );
  expect(reopened.engineering_brief.components).toEqual(
    configured.engineering_brief.components,
  );
  expect(errors).toEqual([]);
});

test("interfaces generate an editable proposal without replacing the live model; offers stay transient", async ({
  page,
  request,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/");
  await openEngineering(page);
  await page.getByRole("button", { name: "Interfaces", exact: true }).click();
  await page.getByRole("button", { name: "Load example", exact: true }).click();
  const before = await (await request.get("/api/project")).json();
  // Adapter uses explicit synthetic sample dimensions; no real fit assertion.
  await page.getByRole("button", { name: /Generate.*adapter/i }).click();
  await expect(
    page.getByRole("button", {
      name: "Download editable adapter proposal",
      exact: true,
    }),
  ).toBeVisible();
  expect(await (await request.get("/api/project")).json()).toEqual(before);
  await page.getByRole("button", { name: "Purchasing", exact: true }).click();
  await page.getByRole("button", { name: "Load example", exact: true }).click();
  await page.getByRole("button", { name: "Evaluate supplied offers" }).click();
  await expect(
    page.getByRole("table", { name: "Purchased and custom BOM" }),
  ).toBeVisible();
  await expect(page.getByText("34.80", { exact: true })).toBeVisible();
  const source = page.getByLabel("Procurement JSON", { exact: true });
  const data = JSON.parse(await source.inputValue());
  data.bom.lines[0].quantity++;
  await source.fill(JSON.stringify(data, null, 2));
  await expect(page.getByText("34.80", { exact: true })).toHaveCount(0);
  expect(await (await request.get("/api/project")).json()).toEqual(before);
  await page.reload();
  await openEngineering(page);
  await page.getByRole("button", { name: "Purchasing", exact: true }).click();
  await expect(
    page.getByLabel("Procurement JSON", { exact: true }),
  ).toHaveValue("{}");
  expect(errors).toEqual([]);
});

test("manufacturing attaches supplied source bytes without implying vendor verification", async ({
  page,
  request,
}) => {
  await page.goto("/");
  await openEngineering(page);
  await page
    .getByRole("button", { name: "Manufacturing", exact: true })
    .click();
  await page.getByRole("button", { name: "Load example", exact: true }).click();
  await page
    .getByLabel("Supplier source excerpt", { exact: true })
    .fill("Synthetic browser fixture. Not a vendor capability statement.");
  await page
    .getByRole("button", { name: "Attach source evidence", exact: true })
    .click();
  // The normalized source capture is visible in the actual controlled profile.
  await expect
    .poll(async () => page.getByLabel("Capability profile JSON").inputValue())
    .toContain("supplier-sources/");
  // Prepare the actual current geometry; earlier suites may have saved a new dimension.
  const geometry = await (await request.get("/api/state")).json();
  if (geometry.geometry_state !== "current") {
    const job = await (
      await request.post("/api/jobs/build", { data: {} })
    ).json();
    await expect
      .poll(
        async () =>
          (await (await request.get(`/api/jobs/${job.id}`)).json()).state,
        { timeout: 30000 },
      )
      .toBe("succeeded");
  }
  await page
    .getByLabel("Handoff section height (mm)", { exact: true })
    .fill("2");
  await page
    .getByLabel("Handoff overhang angle (degrees)", { exact: true })
    .fill("45");
  await page
    .getByRole("button", { name: "Create design handoff", exact: true })
    .click();
  await expect
    .poll(async () => page.getByLabel("Quote request JSON").inputValue())
    .toContain("handoffs/");
  await page
    .getByRole("button", { name: "Create manual request package", exact: true })
    .click();
  await expect(page.getByText("manual_handoff", { exact: true })).toBeVisible();
  expect(
    (await (await request.get("/api/project")).json()).engineering_brief,
  ).toBeDefined();
  await expect(
    page.getByText("Supplied text remains unverified.", { exact: false }),
  ).toBeVisible();
  await page
    .getByText("manual_handoff", { exact: true })
    .scrollIntoViewIfNeeded();
  await page.screenshot({
    path: "test-results/engineering-supplier.png",
    fullPage: true,
  });
});
