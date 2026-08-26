import { describe, expect, it } from "vitest";
import {
  ESRI_CANVAS_ATTRIBUTION,
  ESRI_DARK_BASEMAP_URL,
  ESRI_LIGHT_BASEMAP_URL,
  OPENFREE_ATTRIBUTION,
  OPENFREE_RELIEF_URL
} from "./basemaps";

describe("raster basemaps", () => {
  it("use public ArcGIS Canvas tiles without the retired CARTO endpoint", () => {
    for (const url of [ESRI_DARK_BASEMAP_URL, ESRI_LIGHT_BASEMAP_URL]) {
      expect(url).toContain("server.arcgisonline.com");
      expect(url).toContain("/MapServer/tile/{z}/{y}/{x}");
      expect(url).not.toContain("carto");
    }
  });

  it("keeps the data-provider attribution", () => {
    expect(ESRI_CANVAS_ATTRIBUTION).toContain("OpenStreetMap contributors");
    expect(ESRI_CANVAS_ATTRIBUTION).toContain("Esri");
    expect(OPENFREE_ATTRIBUTION).toContain("OpenFreeMap");
    expect(OPENFREE_ATTRIBUTION).toContain("OpenStreetMap contributors");
  });

  it("uses the no-label OpenFreeMap relief for the overview", () => {
    expect(OPENFREE_RELIEF_URL).toContain("tiles.openfreemap.org/natural_earth");
    expect(OPENFREE_RELIEF_URL).toContain("/{z}/{x}/{y}.png");
    expect(OPENFREE_RELIEF_URL).not.toContain("api_key");
  });
});
