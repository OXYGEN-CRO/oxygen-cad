import { test, expect } from "@playwright/test";

test("light surfaces stay coherent across studio views and compact widths", async ({
  page,
  request,
}) => {
  const before = await (await request.get("/api/project")).json();
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.addInitScript(() =>
    localStorage.setItem("oxygen-cad-theme", "light"),
  );
  await page.goto("/");
  await expect(page.locator("canvas")).toBeVisible();
  for (const width of [320, 390, 768, 1024, 1512]) {
    await page.setViewportSize({ width, height: 982 });
    for (const view of [
      "Project settings",
      "Study setup",
      "History",
      "Source",
    ]) {
      await page
        .getByRole("button", { name: "Project tools", exact: true })
        .click();
      await page.getByRole("menuitem", { name: view, exact: true }).click();
      await expect
        .poll(
          () =>
            page.evaluate(() => {
              const surface = getComputedStyle(
                document.querySelector(".right-panel")!,
              );
              const field = getComputedStyle(document.querySelector("input")!);
              const rgb = (v: string) =>
                v
                  .match(/[\d.]+/g)!
                  .slice(0, 3)
                  .map(Number);
              return {
                scheme: getComputedStyle(document.documentElement).colorScheme,
                lightPanel: rgb(surface.backgroundColor).every((c) => c > 240),
                lightField: rgb(
                  field.backgroundColor === "rgba(0, 0, 0, 0)"
                    ? getComputedStyle(
                        document.querySelector(".feature-search")!,
                      ).backgroundColor
                    : field.backgroundColor,
                ).every((c) => c > 240),
                readable: rgb(field.color).every((c) => c < 60),
                fits: document.documentElement.scrollWidth <= innerWidth,
              };
            }),
          { message: `${view} in light mode at ${width}px` },
        )
        .toEqual({
          scheme: "light",
          lightPanel: true,
          lightField: true,
          readable: true,
          fits: true,
        });
    }
  }
  expect(await (await request.get("/api/project")).json()).toEqual(before);
  expect(errors).toEqual([]);
});

test("theme switching preserves the viewport and draft, persists, and keeps field focus quiet", async ({
  page,
  request,
}) => {
  const before = await (await request.get("/api/project")).json();
  await page.goto("/");
  await expect(page.locator("canvas")).toBeVisible();
  const canvas = await page.locator("canvas").elementHandle();
  const clearColor = () =>
    page.locator("canvas").evaluate((element) => {
      const gl = (element as HTMLCanvasElement).getContext("webgl2")!;
      return Array.from(
        gl.getParameter(gl.COLOR_CLEAR_VALUE) as Float32Array,
      ).slice(0, 3);
    });
  await expect
    .poll(async () => (await clearColor()).every((c) => c < 0.2))
    .toBeTruthy();
  await page.getByRole("button", { name: "Switch to light mode" }).click();
  await expect
    .poll(async () => (await clearColor()).every((c) => c > 0.6))
    .toBeTruthy();
  expect(
    await canvas!.evaluate(
      (element) => element === document.querySelector("canvas"),
    ),
  ).toBeTruthy();
  await page.reload();
  await expect(
    page.getByRole("button", { name: "Switch to dark mode" }),
  ).toBeVisible();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await page.locator(".left-panel .tree-item").first().click();
  await page.getByText("Feature details", { exact: true }).click();
  const width = page.getByLabel("Feature name", { exact: true });
  await width.fill("Theme draft");
  for (const next of ["dark", "light"]) {
    await page.getByRole("button", { name: `Switch to ${next} mode` }).click();
    await expect(width).toHaveValue("Theme draft");
    await width.click();
    const checkFocus = async () => {
      const css = await width.evaluate((element) => {
        const s = getComputedStyle(element);
        return {
          outline: s.outlineStyle,
          shadow: s.boxShadow,
          border: s.borderTopWidth,
        };
      });
      expect(css.outline).toBe("none");
      expect(css.border).toBe("1px");
      expect(
        css.shadow === "none" || css.shadow.includes("inset"),
      ).toBeTruthy();
    };
    await checkFocus();
    await page.keyboard.press("Tab");
    await page.keyboard.press("Shift+Tab");
    await expect(width).toBeFocused();
    await checkFocus();
    await expect(
      page.getByRole("button", { name: "Save changes", exact: true }),
    ).toBeEnabled();
  }
  const tools = page.getByRole("button", {
    name: "Project tools",
    exact: true,
  });
  await tools.focus();
  await page.keyboard.press("ArrowDown");
  await expect(
    page.getByRole("menuitem", { name: "Project settings", exact: true }),
  ).toBeFocused();
  await page.keyboard.press("ArrowDown");
  await expect(
    page.getByRole("menuitem", { name: "Study setup", exact: true }),
  ).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(tools).toBeFocused();
  await expect(page.getByRole("menu")).toHaveCount(0);
  expect(await (await request.get("/api/project")).json()).toEqual(before);
});
