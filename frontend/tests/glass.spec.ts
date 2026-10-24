import { test, expect } from "@playwright/test";

test("production glass theme remains dark and usable across studio views", async ({
  page,
  request,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const before = await (await request.get("/api/project")).json();
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/");
  await expect(page.locator("canvas")).toBeVisible();
  for (const width of [320, 390, 768, 1024, 1512]) {
    await page.setViewportSize({ width, height: 982 });
    for (const view of ["Design", "Verify", "History", "Source"]) {
      if (view === "History" || view === "Source") {
        await page
          .getByRole("button", { name: "Project tools", exact: true })
          .click();
      }
      await page.getByRole("button", { name: view, exact: true }).click();
      await expect
        .poll(
          async () =>
            page.evaluate(() => {
              const rgb = (value: string) =>
                value
                  .match(/[\d.]+/g)!
                  .slice(0, 3)
                  .map(Number);
              const input = getComputedStyle(document.querySelector("input")!);
              const panel = getComputedStyle(
                document.querySelector(".left-panel")!,
              );
              const toolbar = getComputedStyle(
                document.querySelector(".canvas-toolbar > div")!,
              );
              return {
                dark:
                  getComputedStyle(document.documentElement).colorScheme ===
                  "dark",
                darkInput: rgb(input.backgroundColor).every(
                  (channel) => channel < 60,
                ),
                lightText: rgb(input.color).every((channel) => channel > 150),
                rounded: parseFloat(panel.borderRadius) >= 8,
                glass: toolbar.backdropFilter !== "none",
                fits: document.documentElement.scrollWidth <= innerWidth,
              };
            }),
          { message: `${view} appearance and layout at ${width}px` },
        )
        .toEqual({
          dark: true,
          darkInput: true,
          lightText: true,
          rounded: true,
          glass: true,
          fits: true,
        });
    }
  }
  const after = await (await request.get("/api/project")).json();
  expect(after).toEqual(before);
  expect(errors).toEqual([]);
});
