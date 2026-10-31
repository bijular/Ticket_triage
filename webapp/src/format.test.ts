import { describe, expect, it } from "vitest";
import { DIMS, RUBRIC, fmtDur, fmtUsd, kindLabel, pct } from "./format";

describe("format helpers", () => {
  it("computes percentages safely", () => {
    expect(pct(1, 3)).toBe(33);
    expect(pct(5, 0)).toBe(0);
  });
  it("formats durations and money", () => {
    expect(fmtDur(45)).toBe("45s");
    expect(fmtDur(600)).toBe("10m");
    expect(fmtDur(null)).toBe("—");
    expect(fmtUsd(1.234)).toBe("$1.23");
    expect(fmtUsd(undefined)).toBe("—");
  });
  it("labels dataset kinds", () => {
    expect(kindLabel("holdout")).toBe("Hold-out");
    expect(kindLabel("canary_optional")).toBe("Canary (opt.)");
  });
  it("has a 0/3/5 rubric anchor for every scored dimension", () => {
    expect(DIMS).toHaveLength(6);
    for (const d of DIMS) expect(RUBRIC[d]).toHaveLength(3);
  });
});
