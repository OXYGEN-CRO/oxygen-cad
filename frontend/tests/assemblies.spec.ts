import { expect, test } from "@playwright/test";

// Run against a disposable server initialized from examples/assemblies/slider.json.
// O3D_BROWSER_URL selects the integrated shell; this test never opens a live project.
test("assembly panel drives swept contact, persists home and exports BOM in both themes", async ({
  page,
  request,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/");
  await page
    .getByRole("combobox", { name: "Workspace", exact: true })
    .selectOption("assemblies");
  const panel = page.getByRole("region", { name: "Assemblies workspace" });
  await expect(panel.getByText("2 occurrences · 1 joints")).toBeVisible();
  const carriage = panel
    .locator(".assembly-occurrence")
    .filter({ hasText: "Carriage:1" });
  await carriage.getByRole("checkbox").check();
  await panel.getByRole("button", { name: "Joints", exact: true }).click();
  const project = await (await request.get("/api/project")).json();
  const joint = project.native_document.assembly.joints.find(
    (item: { name: string }) => item.name === "Rail",
  );
  await panel
    .getByRole("combobox", { name: "Joint", exact: true })
    .selectOption(String(joint.id));
  const coordinate = panel.getByRole("spinbutton", {
    name: /^distance \(mm\)/,
  });
  await coordinate.fill("-30");
  await panel.getByRole("button", { name: "Drive", exact: true }).click();
  await expect
    .poll(async () => {
      const current = await (await request.get("/api/project")).json();
      return Math.abs(
        current.native_document.assembly.joints.find(
          (item: { id: number }) => item.id === joint.id,
        ).values[0],
      );
    })
    .toBeLessThan(0.002);
  await expect(
    panel.getByRole("region", { name: "Assembly result" }),
  ).toContainText("stopped_by_contact");
  await expect(
    panel.getByRole("button", { name: "Save home pose", exact: true }),
  ).toBeEnabled();
  await panel
    .getByRole("button", { name: "Save home pose", exact: true })
    .click();
  await expect
    .poll(
      async () =>
        (await (await request.get("/api/project")).json()).domains.assemblies
          ?.poses?.length,
    )
    .toBe(1);
  await expect(
    panel.getByRole("button", { name: "Restore Home", exact: true }),
  ).toBeVisible();
  await panel.getByRole("button", { name: "BOM", exact: true }).click();
  await panel
    .getByLabel("Part number", { exact: true })
    .fill("CARRIAGE-BROWSER");
  await panel
    .getByRole("button", { name: "Save part properties", exact: true })
    .click();
  await expect(
    panel.getByRole("button", { name: "Reconcile BOM", exact: true }),
  ).toBeEnabled();
  await panel
    .getByRole("button", { name: "Reconcile BOM", exact: true })
    .click();
  await expect(panel.getByRole("table")).toContainText("CARRIAGE-BROWSER");
  await panel.getByRole("button", { name: "Export CSV", exact: true }).click();
  const download = panel.getByRole("link", {
    name: "Download artifact",
    exact: true,
  });
  await expect(download).toBeVisible();
  const csv = await (
    await request.get((await download.getAttribute("href"))!)
  ).text();
  expect(csv).toContain("CARRIAGE-BROWSER");
  expect(csv).toContain("quantity");
  await expect
    .poll(
      async () =>
        (await (await request.get("/api/project")).json()).domains.assemblies
          ?.reports?.length,
    )
    .toBe(1);
  await page
    .getByRole("button", { name: "Switch to light mode", exact: true })
    .click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await page.screenshot({
    path: "test-results/assemblies-light.png",
    fullPage: true,
  });
  await page
    .getByRole("button", { name: "Switch to dark mode", exact: true })
    .click();
  await page.reload();
  await page
    .getByRole("combobox", { name: "Workspace", exact: true })
    .selectOption("assemblies");
  await expect(panel.getByText("2 occurrences · 1 joints")).toBeVisible();
  await panel.getByRole("button", { name: "Joints", exact: true }).click();
  await expect(
    panel.getByRole("button", { name: "Restore Home", exact: true }),
  ).toBeVisible();
  await expect(
    panel.getByText("Saved reports (1)", { exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: "test-results/assemblies-dark.png",
    fullPage: true,
  });
  expect(errors).toEqual([]);
});
