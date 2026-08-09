import { describe, expect, it } from "vitest";
import {
  detailedPlaceLabelMinZoom,
  placeLabelCellKeys
} from "./placeLabels";

describe("detailed place labels", () => {
  it("показывает малые города раньше сел и внутригородских частей", () => {
    expect(detailedPlaceLabelMinZoom(7_000, "PPL")).toBe(9);
    expect(detailedPlaceLabelMinZoom(1_500, "PPL")).toBe(9.35);
    expect(detailedPlaceLabelMinZoom(80, "PPL")).toBe(10.4);
    expect(detailedPlaceLabelMinZoom(0, "PPL")).toBe(10.8);
    expect(detailedPlaceLabelMinZoom(0, "PPLX")).toBe(12);
  });

  it("возвращает только существующие клетки видимого окна", () => {
    const available = new Set(["18_22", "19_22", "20_22", "19_23"]);

    expect(placeLabelCellKeys([37.8, 44.9, 40.1, 46.2], 2, available)).toEqual([
      "18_22",
      "19_22",
      "20_22",
      "19_23"
    ]);
  });

  it("не строит запросы для битого экстента", () => {
    expect(placeLabelCellKeys([0, Number.NaN, 1, 2], 2, new Set())).toEqual([]);
    expect(placeLabelCellKeys([0, 0, 1, 1], 0, new Set())).toEqual([]);
  });
});
