import { describe, expect, it } from "vitest";
import { computeLayers, money } from "./graph";

describe("computeLayers", () => {
  it("groups independent tasks into the same layer", () => {
    const layers = computeLayers([
      { id: "a", depends_on: [] }, { id: "b", depends_on: [] }, { id: "c", depends_on: ["a", "b"] }, { id: "d", depends_on: ["c"] },
    ]);
    expect(layers).toEqual([["a", "b"], ["c"], ["d"]]);
  });
  it("ignores unknown dependencies and survives cycles", () => {
    expect(computeLayers([{ id: "a", depends_on: ["zzz"] }])).toEqual([["a"]]);
    expect(() => computeLayers([{ id: "a", depends_on: ["b"] }, { id: "b", depends_on: ["a"] }])).not.toThrow();
  });
});

describe("money", () => {
  it("never invents a price", () => {
    expect(money(null)).toBe("unknown");
    expect(money(0)).toBe("$0.00");
    expect(money(0.0042)).toBe("$0.0042");
  });
});
