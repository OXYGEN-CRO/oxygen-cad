import { useState } from "react";

export type Theme = "dark" | "light";
const key = "oxygen-cad-theme";

function applyTheme(theme: Theme) {
  document.documentElement.dataset.theme = theme;
  document
    .querySelector('meta[name="theme-color"]')
    ?.setAttribute("content", theme === "dark" ? "#0b0b0b" : "#f3f3f1");
}

// Run before React mounts. A stored choice wins; existing users start in dark mode.
let initialTheme: Theme = "dark";
try {
  if (localStorage.getItem(key) === "light") initialTheme = "light";
} catch {
  /* Storage may be unavailable in a restricted browser. */
}
applyTheme(initialTheme);

export function useTheme() {
  const [theme, setTheme] = useState<Theme>(initialTheme);
  function toggleTheme() {
    const next = theme === "dark" ? "light" : "dark";
    applyTheme(next);
    setTheme(next);
    try {
      localStorage.setItem(key, next);
    } catch {
      /* The current session still works. */
    }
  }
  return { theme, toggleTheme };
}
