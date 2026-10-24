import { test, expect } from "@playwright/test";

test("graphite components stay neutral, dark and usable across studio views", async ({
  page,
  request,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const before = await (await request.get("/api/project")).json();
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/");
  await expect(page.locator("canvas")).toBeVisible();
  await page.evaluate(() => document.fonts.ready);
  for (const width of [320, 390, 768, 1024, 1512]) {
    await page.setViewportSize({ width, height: 982 });
    for (const view of ["Model", "Study setup", "History", "Source"]) {
      if (view !== "Model") {
        await page
          .getByRole("button", { name: "Project tools", exact: true })
          .click();
      }
      if (view === "Model") {
        const close = page.getByRole("button", {
          name: "Collapse inspector sidebar",
          exact: true,
        });
        if (await close.isVisible()) await close.click();
        await page
          .getByRole("button", { name: "Mounting plate", exact: false })
          .click();
      } else
        await page
          .getByRole("group", { name: "Project tools menu" })
          .getByRole("button", { name: view, exact: true })
          .click();
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
              const neutral = (value: string) => {
                const channels = rgb(value);
                return Math.max(...channels) - Math.min(...channels) < 3;
              };
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
                neutral:
                  neutral(toolbar.backgroundColor) &&
                  neutral(input.color) &&
                  neutral(getComputedStyle(document.body).backgroundColor),
                geist:
                  getComputedStyle(document.body).fontFamily.includes(
                    "Geist Variable",
                  ) &&
                  [...document.fonts].some(
                    (font) =>
                      font.family.includes("Geist Variable") &&
                      font.status === "loaded",
                  ),
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
          neutral: true,
          geist: true,
        });
    }
  }
  const closeInspector = page.getByRole("button", {
    name: "Collapse inspector sidebar",
    exact: true,
  });
  if (await closeInspector.isVisible()) await closeInspector.click();
  await page
    .getByRole("button", { name: "Mounting plate", exact: false })
    .click();
  await page.getByLabel("Width", { exact: true }).fill("64");
  const save = page.getByRole("button", { name: "Save changes", exact: true });
  await expect(save).toBeEnabled();
  for (const hovered of [false, true]) {
    if (hovered) await save.hover();
    await expect
      .poll(() =>
        save.evaluate((element) => {
          const style = getComputedStyle(element);
          const channels = style.backgroundColor
            .match(/[\d.]+/g)!
            .slice(0, 3)
            .map(Number);
          return (
            channels.every((channel) => channel < 70) &&
            Math.max(...channels) - Math.min(...channels) < 3 &&
            style.backgroundImage === "none"
          );
        }),
      )
      .toBeTruthy();
  }
  const after = await (await request.get("/api/project")).json();
  expect(after).toEqual(before);
  expect(errors).toEqual([]);
});
