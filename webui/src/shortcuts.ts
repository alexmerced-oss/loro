/**
 * Keyboard model.
 *
 * These are terminal-native tools whose users live on the keyboard, but the
 * app previously bound exactly one key (Enter, to send). Everything below is
 * a global binding registered once.
 *
 * Two rules keep the bindings from fighting the user:
 *   - plain single-key bindings never fire while a text field has focus, so
 *     typing "n" in the composer types an "n";
 *   - modified bindings still fire there, so Cmd/Ctrl-K works mid-sentence.
 */
export type Shortcut = {
  /** KeyboardEvent.key, compared case-insensitively. */
  key: string;
  /** Require Cmd on macOS / Ctrl elsewhere. */
  mod?: boolean;
  shift?: boolean;
  /** Human-readable, for the shortcuts sheet. */
  describe: string;
  run: (event: KeyboardEvent) => void;
};

function isTextEntry(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  if (!el || !el.tagName) return false;
  const tag = el.tagName.toLowerCase();
  return tag === "input" || tag === "textarea" || tag === "select" || el.isContentEditable;
}

function matches(event: KeyboardEvent, shortcut: Shortcut): boolean {
  if (event.key.toLowerCase() !== shortcut.key.toLowerCase()) return false;
  const mod = event.metaKey || event.ctrlKey;
  if (Boolean(shortcut.mod) !== mod) return false;
  if (Boolean(shortcut.shift) !== event.shiftKey) return false;
  // Alt is never part of a binding here; let the OS have it.
  if (event.altKey) return false;
  return true;
}

export function registerShortcuts(shortcuts: Shortcut[]): () => void {
  function onKeyDown(event: KeyboardEvent) {
    for (const shortcut of shortcuts) {
      if (!matches(event, shortcut)) continue;
      // An unmodified letter is a keystroke first and a command second.
      if (!shortcut.mod && shortcut.key.length === 1 && isTextEntry(event.target)) continue;
      event.preventDefault();
      shortcut.run(event);
      return;
    }
  }
  window.addEventListener("keydown", onKeyDown);
  return () => window.removeEventListener("keydown", onKeyDown);
}

/** Render a binding the way the host platform writes it. */
export function chord(shortcut: Shortcut): string {
  const mac = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform || "");
  const parts: string[] = [];
  if (shortcut.mod) parts.push(mac ? "⌘" : "Ctrl");
  if (shortcut.shift) parts.push(mac ? "⇧" : "Shift");
  parts.push(shortcut.key === "Escape" ? "Esc" : shortcut.key.toUpperCase());
  return parts.join(mac ? "" : "+");
}
