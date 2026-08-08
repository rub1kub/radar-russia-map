import { describe, expect, it } from "vitest";
import featuredPlaces from "../public/data/featured-places.json";

type PlaceBoundary = {
  id: string;
  properties: {
    id: string;
    name: string;
    zone: string;
    region: string;
    district: string;
  };
  geometry: {
    type: "Polygon";
    coordinates: number[][][];
  };
};

const data = featuredPlaces as { type: "FeatureCollection"; features: PlaceBoundary[] };

describe("featured place boundaries", () => {
  it("содержат оба русских названия и совпадают с source_id поиска", () => {
    expect(data.features.map((feature) => [feature.properties.id, feature.properties.name])).toEqual([
      ["519835", "Новая Адыгея"],
      ["469844", "Яблоновский"]
    ]);
  });

  it("принадлежат Тахтамукайскому району Адыгеи", () => {
    for (const feature of data.features) {
      expect(feature.properties.district).toBe("Тахтамукайский район");
      expect(feature.properties.region).toBe("28173009B41832925814017");
      expect(feature.properties.zone).toMatch(/_takhtamukayskiy_rayon_adygeya$/);
    }
  });

  it("содержат замкнутые полигоны в окрестностях Краснодара", () => {
    for (const feature of data.features) {
      expect(feature.geometry.type).toBe("Polygon");
      const ring = feature.geometry.coordinates[0];
      expect(ring.length).toBeGreaterThan(20);
      expect(ring[0]).toEqual(ring[ring.length - 1]);
      for (const [lon, lat] of ring) {
        expect(lon).toBeGreaterThan(38.8);
        expect(lon).toBeLessThan(39.1);
        expect(lat).toBeGreaterThan(44.8);
        expect(lat).toBeLessThan(45.2);
      }
    }
  });
});
