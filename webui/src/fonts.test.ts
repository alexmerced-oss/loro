/// <reference types="node" />
import { readFileSync, readdirSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

// Every font declaration must end in a generic family (or use a --font-* token that does), so a
// machine without a bundled or named font never falls back to the browser's default serif.
const GENERIC = new Set([
  "serif",
  "sans-serif",
  "monospace",
  "cursive",
  "fantasy",
  "system-ui",
  "ui-serif",
  "ui-sans-serif",
  "ui-monospace",
  "ui-rounded",
  "math",
  "emoji",
  "fangsong",
]);
const KEYWORDS = new Set(["inherit", "initial", "unset", "revert", "revert-layer"]);
const SRC = resolve(import.meta.dirname);

function stylesheets(): { name: string; css: string }[] {
  return readdirSync(SRC)
    .filter((name) => name.endsWith(".css"))
    .map((name) => ({ name, css: readFileSync(resolve(SRC, name), "utf8") }));
}

function withoutFontFaces(css: string): string {
  return css.replace(/\/\*[\s\S]*?\*\//g, "").replace(/@font-face\s*{[^}]*}/g, "");
}

function lastFamily(list: string): string {
  const families = list.split(",").map((item) => item.trim().replace(/^["']|["']$/g, ""));
  return families[families.length - 1] ?? "";
}

/** Problems for one declaration value; empty when it is safe. */
export function fontProblems(property: string, value: string): string[] {
  const text = value.replace(/!important/, "").trim();
  if (KEYWORDS.has(text) || /var\(--font-(sans|mono)\)/.test(text)) return [];
  if (property === "font-family") {
    return GENERIC.has(lastFamily(text)) ? [] : [`${property}: ${value}`];
  }
  // `font` shorthand: [style] [weight] size[/line-height] family-list
  const match = text.match(/(?:^|\s)[\d.]+(?:px|em|rem|%|pt|vh|vw)(?:\/\S+)?\s+(.+)$/);
  if (!match) return KEYWORDS.has(text) ? [] : [`${property}: ${value} (no family list)`];
  return GENERIC.has(lastFamily(match[1])) ? [] : [`${property}: ${value}`];
}

describe("font declarations", () => {
  it("recognizes unsafe declarations", () => {
    expect(fontProblems("font", "500 10px 'DM Mono'")).toHaveLength(1);
    expect(fontProblems("font-family", "Manrope")).toHaveLength(1);
    expect(fontProblems("font", "12px/1.6 'DM Mono',ui-monospace,monospace")).toEqual([]);
    expect(fontProblems("font", "500 10px var(--font-mono)")).toEqual([]);
    expect(fontProblems("font", "inherit")).toEqual([]);
  });

  it("every stylesheet names a generic fallback", () => {
    const problems: string[] = [];
    for (const { name, css } of stylesheets()) {
      const body = withoutFontFaces(css);
      for (const match of body.matchAll(/(?:^|[;{\s])(font|font-family)\s*:\s*([^;}]+)/g)) {
        for (const problem of fontProblems(match[1], match[2])) problems.push(`${name}: ${problem}`);
      }
    }
    expect(problems).toEqual([]);
  });

  it("the font tokens end in generic families", () => {
    const tokens = readFileSync(resolve(SRC, "fonts.css"), "utf8");
    for (const token of ["--font-sans", "--font-mono"]) {
      const value = tokens.match(new RegExp(`${token}:\\s*([^;]+);`))?.[1] ?? "";
      expect(GENERIC.has(lastFamily(value)), `${token}: ${value}`).toBe(true);
    }
  });
});
