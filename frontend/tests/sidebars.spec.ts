import { test, expect } from "@playwright/test";

test("breadcrumbs and panel toggles resize the workspace without losing edits", async ({
  page,
  request,
}) => {
  const before = await (await request.get("/api/project")).json();
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/");
  await expect(page.locator("canvas")).toBeVisible();
  await expect(page.locator(".topbar .brand")).toHaveCount(0);
  await expect(page.locator(".topbar input")).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Design", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Verify", exact: true }),
  ).toHaveCount(0);
  const canvas = page.locator("canvas");
  const initial = (await canvas.boundingBox())!.width;
  await page
    .getByRole("button", { name: "Collapse feature sidebar", exact: true })
    .click();
  await expect(page.locator("#feature-sidebar")).toBeHidden();
  await expect
    .poll(async () => (await canvas.boundingBox())!.width)
    .toBeGreaterThan(initial + 100);
  const expandFeatures = page.getByRole("button", {
    name: "Expand feature sidebar",
    exact: true,
  });
  await expect(expandFeatures).toHaveAttribute("aria-expanded", "false");
  await expandFeatures.focus();
  await page.keyboard.press("Enter");
  await expect(page.locator("#feature-sidebar")).toBeVisible();
  await page
    .getByRole("button", { name: "Mounting plate", exact: false })
    .click();
  await page.getByLabel("Width", { exact: true }).fill("68");
  const breadcrumb = page.getByRole("navigation", { name: "Breadcrumb" });
  const featureCrumb = breadcrumb.getByRole("button", {
    name: "Mounting plate",
    exact: true,
  });
  await featureCrumb.click();
  await expect(page.locator("#inspector-sidebar")).toBeHidden();
  await page
    .getByRole("button", { name: "Expand inspector sidebar", exact: true })
    .click();
  await expect(page.getByLabel("Width", { exact: true })).toHaveValue("68");
  await page
    .getByRole("button", { name: "Close properties", exact: true })
    .click();
  await featureCrumb.click();
  await expect(page.getByLabel("Width", { exact: true })).toHaveValue("68");
  await breadcrumb
    .getByRole("button", { name: "Workspace", exact: true })
    .click();
  await expect(page.locator("#feature-sidebar")).toBeHidden();
  await page
    .getByRole("button", { name: "Project tools", exact: true })
    .click();
  await page
    .getByRole("menuitem", { name: "Study setup", exact: true })
    .click();
  await expect(
    page.getByText("Select planar faces in the viewport", { exact: false }),
  ).toHaveCount(0);
  await expect(page.getByText("Static stress", { exact: true })).toBeVisible();
  await page
    .getByRole("button", { name: "Collapse inspector sidebar", exact: true })
    .click();
  await breadcrumb.getByRole("button", { name: "Study", exact: true }).click();
  await expect(page.getByText("Static stress", { exact: true })).toBeVisible();
  await page.setViewportSize({ width: 320, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
  await page
    .getByRole("button", { name: "Collapse inspector sidebar", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Expand inspector sidebar", exact: true })
    .click();
  await expect(page.getByText("Static stress", { exact: true })).toBeVisible();
  const after = await (await request.get("/api/project")).json();
  expect(after).toEqual(before);
  expect(errors).toEqual([]);
});
