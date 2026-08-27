import { describe, expect, it } from "vitest";
import { labelFreeVectorStyle } from "./vectorBasemap";

describe("labelFreeVectorStyle", () => {
  it("keeps map geometry and strips provider labels and raster relief", () => {
    const style = labelFreeVectorStyle({
      version: 8,
      sources: { openmaptiles: { type: "vector" }, relief: { type: "raster" } },
      layers: [
        { id: "background", type: "background" },
        { id: "relief", type: "raster", source: "relief" },
        { id: "water", type: "fill", source: "openmaptiles" },
        { id: "roads", type: "line", source: "openmaptiles" },
        { id: "places", type: "symbol", source: "openmaptiles" }
      ]
    });

    expect(style.layers.map((layer) => layer.id)).toEqual(["water", "roads"]);
  });

  it("rejects an incompatible provider response", () => {
    expect(() => labelFreeVectorStyle({ version: 8, sources: {}, layers: [] }))
      .toThrow("openmaptiles");
  });
});
