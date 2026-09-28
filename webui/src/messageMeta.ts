/** Stop reason, token use, and whether earlier turns were summarized for this reply. */
export function messageMeta(metadata: Record<string, unknown>): string {
  const usage = (metadata.usage || {}) as Record<string, number | undefined>;
  const tokens = usage.total_tokens ?? (usage.input_tokens || 0) + (usage.output_tokens || 0);
  const parts = [String(metadata.stop_reason), `${tokens.toLocaleString()} tokens`];
  const compacted = Number((metadata.context as Record<string, unknown> | undefined)?.compacted_messages || 0);
  if (compacted > 0) parts.push(`${compacted} earlier message${compacted === 1 ? "" : "s"} summarized`);
  return parts.join(" · ");
}

/** Start a new paragraph when a later model step streams, so step texts never run together. */
export function streamStepBreak(current: string): string {
  if (!current.trim() || current.endsWith("\n\n")) return current;
  return `${current.replace(/\s+$/, "")}\n\n`;
}
