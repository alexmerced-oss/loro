/**
 * Theme selection.
 *
 * The UI was authored dark-only and ignored the OS setting entirely. It now
 * has three states, matching how browsers actually behave:
 *
 *   "system"  no stamp on <html>; `prefers-color-scheme` decides
 *   "dark"    data-theme="dark",  wins over a light OS setting
 *   "light"   data-theme="light", wins over a dark OS setting
 *
 * The design is dark-first, so the bare :root block in styles.css holds the
 * dark palette and the light palette is applied by the media query and by the
 * explicit light stamp.
 */
export type ThemeChoice = "system" | "light" | "dark";

const STORAGE_KEY = "loro-theme";

export function readTheme(): ThemeChoice {
  if (typeof localStorage === "undefined") return "system";
  const stored = localStorage.getItem(STORAGE_KEY);
  return stored === "light" || stored === "dark" ? stored : "system";
}

export function applyTheme(choice: ThemeChoice): void {
  if (typeof document === "undefined") return;
  const root = document.documentElement;
  if (choice === "system") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", choice);
}

export function storeTheme(choice: ThemeChoice): void {
  if (typeof localStorage === "undefined") return;
  if (choice === "system") localStorage.removeItem(STORAGE_KEY);
  else localStorage.setItem(STORAGE_KEY, choice);
}

/** Cycle order matches the control's label: system, then light, then dark. */
export function nextTheme(choice: ThemeChoice): ThemeChoice {
  return choice === "system" ? "light" : choice === "light" ? "dark" : "system";
}

export function themeLabel(choice: ThemeChoice): string {
  return choice === "system" ? "Match system" : choice === "light" ? "Light" : "Dark";
}

export function themeGlyph(choice: ThemeChoice): string {
  return choice === "system" ? "◐" : choice === "light" ? "☀" : "☾";
}

/** Applied before React mounts so the first paint is already correct. */
export function initTheme(): ThemeChoice {
  const choice = readTheme();
  applyTheme(choice);
  return choice;
}
