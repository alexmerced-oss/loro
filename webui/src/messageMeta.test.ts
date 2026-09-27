import { describe, expect, it } from "vitest";
import { messageMeta } from "./messageMeta";

describe("messageMeta", () => {
  it("adds input and output tokens when no total is reported", () => {
    expect(messageMeta({ stop_reason: "completed", usage: { input_tokens: 1200, output_tokens: 34 } })).toBe(
      `completed · ${(1234).toLocaleString()} tokens`,
    );
  });

  it("prefers a reported total", () => {
    expect(messageMeta({ stop_reason: "stop", usage: { total_tokens: 7 } })).toBe("stop · 7 tokens");
  });

  it("says when earlier turns were summarized", () => {
    expect(messageMeta({ stop_reason: "completed", usage: {}, context: { compacted_messages: 1 } })).toBe(
      "completed · 0 tokens · 1 earlier message summarized",
    );
    expect(messageMeta({ stop_reason: "completed", usage: {}, context: { compacted_messages: 6 } })).toContain(
      "6 earlier messages summarized",
    );
  });
});
