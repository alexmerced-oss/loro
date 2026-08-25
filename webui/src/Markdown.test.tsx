import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Markdown } from "./Markdown";

describe("Markdown", () => {
  it("renders emphasis and inline code as elements, not literal characters", () => {
    const { container } = render(<Markdown>{"Change **the rate** in `pricing.py`."}</Markdown>);
    expect(container.querySelector("strong")?.textContent).toBe("the rate");
    expect(container.querySelector("code.inline-code")?.textContent).toBe("pricing.py");
    expect(container.textContent).not.toContain("**");
    expect(container.textContent).not.toContain("`");
  });

  it("renders headings and lists", () => {
    const { container } = render(<Markdown>{"## Findings\n\n- first\n- second\n"}</Markdown>);
    expect(container.querySelector("h2")?.textContent).toBe("Findings");
    expect(container.querySelectorAll("li")).toHaveLength(2);
  });

  it("renders a fenced block with a copy control", () => {
    const { container } = render(<Markdown>{"```python\nprint('hi')\n```"}</Markdown>);
    expect(container.querySelector(".code-block pre code")?.textContent).toBe("print('hi')");
    expect(screen.getByRole("button", { name: /copy code/i })).toBeInTheDocument();
  });

  it("renders GFM tables", () => {
    render(<Markdown>{"| a | b |\n| - | - |\n| 1 | 2 |\n"}</Markdown>);
    expect(screen.getByRole("table")).toBeInTheDocument();
  });

  // Model output can quote a hostile file, a scraped page, or a tool result.
  it("never turns embedded HTML into elements", () => {
    const hostile = '<img src=x onerror="window.__pwned=1"> and <script>window.__pwned=2</script>';
    const { container } = render(<Markdown>{hostile}</Markdown>);
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("script")).toBeNull();
    expect((window as unknown as { __pwned?: number }).__pwned).toBeUndefined();
    expect(container.textContent).toContain("onerror");
  });

  it("does not let a link steal the opener", () => {
    const { container } = render(<Markdown>{"[docs](https://example.com)"}</Markdown>);
    const anchor = container.querySelector("a");
    expect(anchor?.getAttribute("target")).toBe("_blank");
    expect(anchor?.getAttribute("rel")).toContain("noopener");
    expect(anchor?.getAttribute("rel")).toContain("noreferrer");
  });

  it("leaves a javascript: URL inert", () => {
    const { container } = render(<Markdown>{"[click](javascript:alert(1))"}</Markdown>);
    const href = container.querySelector("a")?.getAttribute("href") ?? "";
    expect(href.toLowerCase().startsWith("javascript:")).toBe(false);
  });
});
