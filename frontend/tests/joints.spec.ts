import { test, expect } from "@playwright/test";

test("joint selection persists task and source contracts and invalidates changed evidence", async ({
  page,
  request,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/");
  await page
    .getByRole("button", { name: "Project tools", exact: true })
    .click();
  await page
    .getByRole("menuitem", { name: "Joint selection", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Load illustrative elbow", exact: true })
    .click();
  await expect(page.getByLabel("Payload mass", { exact: true })).toHaveValue(
    "1",
  );
  await page
    .getByRole("checkbox", { name: "Confirm static model", exact: true })
    .check();
  await page.getByRole("button", { name: "Save changes", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Save changes", exact: true }),
  ).toHaveCount(0);
  await page.getByLabel("Payload mass unit", { exact: true }).selectOption("g");
  await expect(page.getByLabel("Payload mass", { exact: true })).toHaveValue(
    "1000",
  );
  await page
    .getByLabel("Payload mass unit", { exact: true })
    .selectOption("kg");
  await expect(page.getByLabel("Payload mass", { exact: true })).toHaveValue(
    "1",
  );
  await page.getByRole("button", { name: "Save changes", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Save changes", exact: true }),
  ).toHaveCount(0);
  const saved = await (await request.get("/api/project")).json();
  expect(saved.engineering_brief.components).toHaveLength(12);
  expect(saved.engineering_brief.joint_requirements[0].payload_arm).toEqual({
    value: 300,
    unit: "mm",
  });
  await page
    .getByRole("button", { name: "Evaluate joint selection", exact: true })
    .click();
  await expect(
    page.getByText("Static demand: 3.5316 N*m", { exact: true }),
  ).toBeVisible();
  const first = await (await request.get("/api/joints/status")).json();
  expect(first.freshness).toBe("current");
  expect(first.report.state).toBe("unknown");
  expect(
    first.report.requirements[0].candidates.find(
      (c: any) => c.mpn === "XM430-W350-R",
    ).status,
  ).toBe("failed");
  const selected = page
    .locator(".joint-candidate")
    .filter({ has: page.locator("summary", { hasText: "XM430-W350-T" }) });
  await expect(
    selected.getByText("sustained · unknown", { exact: true }),
  ).toBeVisible();
  await page.reload();
  await page
    .getByRole("button", { name: "Project tools", exact: true })
    .click();
  await page
    .getByRole("menuitem", { name: "Joint selection", exact: true })
    .click();
  await expect(
    page.getByText("Static demand: 3.5316 N*m", { exact: true }),
  ).toBeVisible();
  await page.getByLabel("Payload moment arm", { exact: true }).fill("400");
  await expect(
    page.getByText("Unsaved changes · save before evaluating", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Evaluate joint selection", exact: true }),
  ).toBeDisabled();
  await page.getByRole("button", { name: "Save changes", exact: true }).click();
  await expect(
    page.getByText(
      "Earlier evidence belongs to different inputs. Re-evaluate the saved design.",
      { exact: true },
    ),
  ).toBeVisible();
  expect(
    (await (await request.get("/api/joints/status")).json()).freshness,
  ).toBe("stale");
  await page
    .getByRole("button", { name: "Evaluate joint selection", exact: true })
    .click();
  await expect(
    page.getByText("Static demand: 4.5126 N*m", { exact: true }),
  ).toBeVisible();
  const after = await (await request.get("/api/joints/status")).json();
  expect(after.report.state).toBe("failed");
  expect(after.input_key).not.toBe(first.input_key);
  for (const width of [390, 1512]) {
    await page.setViewportSize({ width, height: 982 });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBeTruthy();
  }
  await page.screenshot({
    path: "test-results/joint-selection.png",
    fullPage: true,
  });
  expect(errors).toEqual([]);
});
