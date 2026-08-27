import { describe, expect, it } from "vitest";
import {
  ESRI_IMAGERY_ATTRIBUTION,
  ESRI_IMAGERY_URL,
  OPENFREE_ATTRIBUTION,
  OPENFREE_DARK_STYLE_URL,
  OPENFREE_LIGHT_STYLE_URL
} from "./basemaps";

describe("basemaps", () => {
  it("uses keyless vector styles for both map themes", () => {
    for (const url of [OPENFREE_DARK_STYLE_URL, OPENFREE_LIGHT_STYLE_URL]) {
      expect(url).toContain("tiles.openfreemap.org/styles/");
      expect(url).not.toContain("api_key");
    }
  });

  it("keeps the data-provider attribution", () => {
    expect(OPENFREE_ATTRIBUTION).toContain("OpenFreeMap");
    expect(OPENFREE_ATTRIBUTION).toContain("OpenStreetMap contributors");
    expect(ESRI_IMAGERY_ATTRIBUTION).toContain("Esri");
  });

  it("keeps satellite tiles as the separate raster mode", () => {
    expect(ESRI_IMAGERY_URL).toContain("World_Imagery/MapServer/tile/{z}/{y}/{x}");
  });
});
