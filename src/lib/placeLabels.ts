export const DETAILED_PLACE_LABEL_ZOOM = 9;

export type DetailedPlaceLabelRow = [
  name: string,
  lat: number,
  lon: number,
  population: number | null,
  code: string
];

export type PlaceLabelManifest = {
  version: number;
  cellSize: number;
  cells: string[];
};

export function detailedPlaceLabelMinZoom(population: number, code: string): number {
  if (code === "PPLA3" || code === "PPLA4" || population >= 5_000) return 9;
  if (population >= 1_000) return 9.35;
  if (population >= 200) return 9.9;
  if (population > 0) return 10.4;
  // PPLX — именованная часть населённого пункта. Она полезна только тогда,
  // когда вокруг уже хватает места для подписей самих сёл и посёлков.
  if (code === "PPLX") return 12;
  return 10.8;
}

export function placeLabelCellKeys(
  extent: number[],
  cellSize: number,
  available: ReadonlySet<string>
): string[] {
  if (extent.length < 4 || !Number.isFinite(cellSize) || cellSize <= 0) return [];

  const [rawWest, rawSouth, rawEast, rawNorth] = extent;
  if (![rawWest, rawSouth, rawEast, rawNorth].every(Number.isFinite)) return [];

  const west = Math.max(-180, Math.min(180, Math.min(rawWest, rawEast)));
  const east = Math.max(-180, Math.min(180 - Number.EPSILON, Math.max(rawWest, rawEast)));
  const south = Math.max(-85, Math.min(85, Math.min(rawSouth, rawNorth)));
  const north = Math.max(-85, Math.min(85 - Number.EPSILON, Math.max(rawSouth, rawNorth)));
  const keys: string[] = [];

  for (let y = Math.floor(south / cellSize); y <= Math.floor(north / cellSize); y += 1) {
    for (let x = Math.floor(west / cellSize); x <= Math.floor(east / cellSize); x += 1) {
      const key = `${x}_${y}`;
      if (available.has(key)) keys.push(key);
    }
  }

  return keys;
}
