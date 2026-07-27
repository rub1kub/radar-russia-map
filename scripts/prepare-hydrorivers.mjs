import { readFileSync, writeFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(fileURLToPath(new URL("../package.json", import.meta.url)));
const outPath = join(root, "research/data_sources/hydrorivers_russia_network.geojson");
const MIN_UPLAND_SKM = 50;
const MIN_DIS_AV_CMS = 0.5;

const sources = [
  {
    zip: "HydroRIVERS_v10_eu_shp.zip",
    base: "HydroRIVERS_v10_eu_shp/HydroRIVERS_v10_eu"
  },
  {
    zip: "HydroRIVERS_v10_si_shp.zip",
    base: "HydroRIVERS_v10_si_shp/HydroRIVERS_v10_si"
  },
  {
    zip: "HydroRIVERS_v10_as_shp.zip",
    base: "HydroRIVERS_v10_as_shp/HydroRIVERS_v10_as"
  }
];

const readJson = (path) => JSON.parse(readFileSync(join(root, path), "utf8"));

const roundNumber = (value, precision) => {
  const factor = 10 ** precision;
  return Math.round(value * factor) / factor;
};

const ringBounds = (ring) => {
  const bounds = [Infinity, Infinity, -Infinity, -Infinity];
  for (const [lon, lat] of ring) {
    bounds[0] = Math.min(bounds[0], lon);
    bounds[1] = Math.min(bounds[1], lat);
    bounds[2] = Math.max(bounds[2], lon);
    bounds[3] = Math.max(bounds[3], lat);
  }
  return bounds;
};

const pointInRing = ([x, y], ring) => {
  let inside = false;
  for (let index = 0, prev = ring.length - 1; index < ring.length; prev = index, index += 1) {
    const [xi, yi] = ring[index];
    const [xj, yj] = ring[prev];
    const intersects = yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi || 1e-12) + xi;
    if (intersects) inside = !inside;
  }
  return inside;
};

const geometryPolygons = (geometry) => {
  if (geometry?.type === "Polygon") return [geometry.coordinates];
  if (geometry?.type === "MultiPolygon") return geometry.coordinates;
  return [];
};

const createRegionMatcher = () => {
  const regions = readJson("public/data/regions.json");
  const polygons = regions.features.flatMap((feature) =>
    geometryPolygons(feature.geometry).flatMap((polygon) => {
      const exterior = polygon[0];
      if (!exterior) return [];
      return [{ exterior, holes: polygon.slice(1), bounds: ringBounds(exterior) }];
    })
  );

  const bboxTouches = ([minLon, minLat, maxLon, maxLat]) =>
    polygons.some((polygon) => {
      const bounds = polygon.bounds;
      return !(maxLon < bounds[0] - 0.2 || minLon > bounds[2] + 0.2 || maxLat < bounds[1] - 0.2 || minLat > bounds[3] + 0.2);
    });

  const pointMatches = ([lon, lat]) =>
    polygons.some((polygon) => {
      const bounds = polygon.bounds;
      if (lon < bounds[0] - 0.2 || lon > bounds[2] + 0.2 || lat < bounds[1] - 0.2 || lat > bounds[3] + 0.2) {
        return false;
      }
      return pointInRing([lon, lat], polygon.exterior) && !polygon.holes.some((hole) => pointInRing([lon, lat], hole));
    });

  return { bboxTouches, pointMatches };
};

const parseDbf = (buffer) => {
  const records = buffer.readUInt32LE(4);
  const headerLength = buffer.readUInt16LE(8);
  const recordLength = buffer.readUInt16LE(10);
  const fields = [];
  let offset = 32;
  let recordOffset = 1;

  while (buffer[offset] !== 0x0d) {
    const raw = buffer.subarray(offset, offset + 32);
    const name = raw.subarray(0, 11).toString("ascii").replace(/\0.*$/, "");
    const length = raw[16];
    fields.push({ name, length, offset: recordOffset });
    recordOffset += length;
    offset += 32;
  }

  const wanted = ["HYRIV_ID", "LENGTH_KM", "UPLAND_SKM", "DIS_AV_CMS", "ORD_FLOW", "ORD_STRA", "ORD_CLAS"];
  const picked = Object.fromEntries(wanted.map((name) => [name, fields.find((field) => field.name === name)]));

  return {
    records,
    get(index) {
      const base = headerLength + index * recordLength;
      return Object.fromEntries(
        wanted.map((name) => {
          const field = picked[name];
          const value = buffer
            .subarray(base + field.offset, base + field.offset + field.length)
            .toString("ascii")
            .trim();
          return [name, Number(value)];
        })
      );
    }
  };
};

const hydroTier = ({ UPLAND_SKM, DIS_AV_CMS }) => {
  if (UPLAND_SKM < MIN_UPLAND_SKM && DIS_AV_CMS < MIN_DIS_AV_CMS) return null;
  if (UPLAND_SKM >= 25000 || DIS_AV_CMS >= 250) return { minZoom: 2.6, widthClass: 5, cellSize: 3 };
  if (UPLAND_SKM >= 5000 || DIS_AV_CMS >= 50) return { minZoom: 3.8, widthClass: 4, cellSize: 2 };
  if (UPLAND_SKM >= 1000 || DIS_AV_CMS >= 10) return { minZoom: 5.2, widthClass: 3, cellSize: 1 };
  if (UPLAND_SKM >= 200 || DIS_AV_CMS >= 2) return { minZoom: 6.6, widthClass: 2, cellSize: 1 };
  return { minZoom: 8.0, widthClass: 1, cellSize: 0.5 };
};

const sampledPointMatches = (points, pointMatches) => {
  if (points.length === 0) return false;
  const step = Math.max(1, Math.floor(points.length / 24));
  for (let index = 0; index < points.length; index += step) {
    if (pointMatches(points[index])) return true;
  }
  return pointMatches(points[points.length - 1]);
};

const readZipBuffer = (zipName, entry) =>
  execFileSync("unzip", ["-p", join(root, "research/data_sources", zipName), entry], {
    maxBuffer: 520 * 1024 * 1024
  });

const grouped = new Map();
let scannedRecords = 0;
let touchedRecords = 0;
let emittedLines = 0;

const { bboxTouches, pointMatches } = createRegionMatcher();

for (const source of sources) {
  const shp = readZipBuffer(source.zip, `${source.base}.shp`);
  const dbf = parseDbf(readZipBuffer(source.zip, `${source.base}.dbf`));
  let offset = 100;

  for (let recordIndex = 0; offset < shp.length && recordIndex < dbf.records; recordIndex += 1) {
    const contentLength = shp.readInt32BE(offset + 4) * 2;
    const start = offset + 8;
    const shapeType = shp.readInt32LE(start);
    offset = start + contentLength;
    scannedRecords += 1;

    if (shapeType !== 3 && shapeType !== 13 && shapeType !== 23) continue;

    const bbox = [
      shp.readDoubleLE(start + 4),
      shp.readDoubleLE(start + 12),
      shp.readDoubleLE(start + 20),
      shp.readDoubleLE(start + 28)
    ];
    if (!bboxTouches(bbox)) continue;

    const attrs = dbf.get(recordIndex);
    const tier = hydroTier(attrs);
    if (!tier) continue;

    const numParts = shp.readInt32LE(start + 36);
    const numPoints = shp.readInt32LE(start + 40);
    const partsStart = start + 44;
    const pointsStart = partsStart + numParts * 4;
    const parts = Array.from({ length: numParts }, (_, index) => shp.readInt32LE(partsStart + index * 4));
    const points = Array.from({ length: numPoints }, (_, index) => {
      const pointOffset = pointsStart + index * 16;
      return [shp.readDoubleLE(pointOffset), shp.readDoubleLE(pointOffset + 8)];
    });

    if (!sampledPointMatches(points, pointMatches)) continue;
    touchedRecords += 1;

    const centerLon = (bbox[0] + bbox[2]) / 2;
    const centerLat = (bbox[1] + bbox[3]) / 2;
    const cellLon = Math.floor(centerLon / tier.cellSize) * tier.cellSize;
    const cellLat = Math.floor(centerLat / tier.cellSize) * tier.cellSize;
    const key = `${tier.minZoom}|${tier.widthClass}|${cellLon}|${cellLat}`;
    let group = grouped.get(key);
    if (!group) {
      group = {
        minZoom: tier.minZoom,
        widthClass: tier.widthClass,
        coordinates: []
      };
      grouped.set(key, group);
    }

    for (let partIndex = 0; partIndex < parts.length; partIndex += 1) {
      const from = parts[partIndex];
      const to = parts[partIndex + 1] ?? points.length;
      const line = points.slice(from, to).map(([lon, lat]) => [roundNumber(lon, 4), roundNumber(lat, 4)]);
      if (line.length < 2) continue;
      group.coordinates.push(line);
      emittedLines += 1;
    }
  }
}

const features = Array.from(grouped.entries())
  .sort(([left], [right]) => left.localeCompare(right))
  .map(([key, group], index) => ({
    type: "Feature",
    id: `hydroriver-${index}`,
    properties: {
      id: `hydroriver-${index}`,
      minZoom: group.minZoom,
      widthClass: group.widthClass,
      lineCount: group.coordinates.length,
      key
    },
    geometry: {
      type: "MultiLineString",
      coordinates: group.coordinates
    }
  }));

writeFileSync(
  outPath,
  JSON.stringify({
    type: "FeatureCollection",
    features
  })
);

console.log(
  `Prepared HydroRIVERS network: ${features.length} grouped features, ${emittedLines} lines, ${touchedRecords}/${scannedRecords} records`
);
