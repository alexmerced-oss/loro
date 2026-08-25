import { describe, expect, it, vi, afterEach } from "vitest";
import { chord, registerShortcuts, type Shortcut } from "./shortcuts";

function press(key: string, init: Partial<KeyboardEventInit> = {}, target?: Element) {
  const event = new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true, ...init });
  (target ?? window).dispatchEvent(event);
  return event;
}

const cleanups: Array<() => void> = [];
afterEach(() => {
  while (cleanups.length) cleanups.pop()!();
  document.body.innerHTML = "";
});

function register(shortcuts: Shortcut[]) {
  cleanups.push(registerShortcuts(shortcuts));
}

describe("registerShortcuts", () => {
  it("runs a modified binding and suppresses the browser default", () => {
    const run = vi.fn();
    register([{ key: "k", mod: true, describe: "focus", run }]);
    const event = press("k", { metaKey: true });
    expect(run).toHaveBeenCalledOnce();
    expect(event.defaultPrevented).toBe(true);
  });

  it("treats Ctrl and Cmd as the same modifier", () => {
    const run = vi.fn();
    register([{ key: "k", mod: true, describe: "focus", run }]);
    press("k", { ctrlKey: true });
    expect(run).toHaveBeenCalledOnce();
  });

  it("ignores a bare key when the binding wants a modifier", () => {
    const run = vi.fn();
    register([{ key: "k", mod: true, describe: "focus", run }]);
    press("k");
    expect(run).not.toHaveBeenCalled();
  });

  it("distinguishes shifted from unshifted bindings", () => {
    const plain = vi.fn();
    const shifted = vi.fn();
    register([
      { key: "n", mod: true, describe: "plain", run: plain },
      { key: "n", mod: true, shift: true, describe: "shifted", run: shifted },
    ]);
    press("n", { metaKey: true, shiftKey: true });
    expect(shifted).toHaveBeenCalledOnce();
    expect(plain).not.toHaveBeenCalled();
  });

  // The rule that keeps the bindings out of the user's way.
  it("does not fire an unmodified letter while typing in a field", () => {
    const run = vi.fn();
    register([{ key: "/", describe: "help", run }]);
    const input = document.createElement("textarea");
    document.body.appendChild(input);
    press("/", {}, input);
    expect(run).not.toHaveBeenCalled();
  });

  it("still fires a modified binding while typing in a field", () => {
    const run = vi.fn();
    register([{ key: "k", mod: true, describe: "focus", run }]);
    const input = document.createElement("textarea");
    document.body.appendChild(input);
    press("k", { metaKey: true }, input);
    expect(run).toHaveBeenCalledOnce();
  });

  it("still fires Escape while typing in a field", () => {
    const run = vi.fn();
    register([{ key: "Escape", describe: "close", run }]);
    const input = document.createElement("input");
    document.body.appendChild(input);
    press("Escape", {}, input);
    expect(run).toHaveBeenCalledOnce();
  });

  it("leaves Alt combinations to the operating system", () => {
    const run = vi.fn();
    register([{ key: "k", mod: true, describe: "focus", run }]);
    press("k", { metaKey: true, altKey: true });
    expect(run).not.toHaveBeenCalled();
  });

  it("stops listening once disposed", () => {
    const run = vi.fn();
    const dispose = registerShortcuts([{ key: "k", mod: true, describe: "focus", run }]);
    dispose();
    press("k", { metaKey: true });
    expect(run).not.toHaveBeenCalled();
  });
});

describe("chord", () => {
  it("writes bindings the way the platform does", () => {
    const original = navigator.platform;
    Object.defineProperty(navigator, "platform", { value: "Win32", configurable: true });
    expect(chord({ key: "k", mod: true, describe: "", run: () => {} })).toBe("Ctrl+K");
    expect(chord({ key: "Escape", describe: "", run: () => {} })).toBe("Esc");
    Object.defineProperty(navigator, "platform", { value: "MacIntel", configurable: true });
    expect(chord({ key: "n", mod: true, shift: true, describe: "", run: () => {} })).toBe("⌘⇧N");
    Object.defineProperty(navigator, "platform", { value: original, configurable: true });
  });
});
