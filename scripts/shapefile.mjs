import { execFileSync } from "node:child_process";
import { join } from "node:path";

const textDecoder = new TextDecoder("utf-8");

const readZipEntry = (root, zipName, entryName, maxBuffer = 160 * 1024 * 1024) =>
  execFileSync("unzip", ["-p", join(root, "research/data_sources", zipName), entryName], {
    maxBuffer
  });

const cleanFieldName = (buffer) => buffer.toString("ascii").replace(/\0.*$/, "");

const parseDbfValue = (rawValue, field) => {
  const value = textDecoder.decode(rawValue).trim();
  if (!value) return null;

  if (field.type === "N" || field.type === "F") {
    const numberValue = Number(value);
    return Number.isFinite(numberValue) ? numberValue : null;
  }

  if (field.type === "L") return /^[YyTt]$/.test(value);
  return value;
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
    const field = {
      name: cleanFieldName(raw.subarray(0, 11)),
      type: String.fromCharCode(raw[11]),
      length: raw[16],
      offset: recordOffset
    };
    fields.push(field);
    recordOffset += field.length;
    offset += 32;
  }

  return {
    records,
    get(index) {
      const base = headerLength + index * recordLength;
      if (buffer[base] === 0x2a) return null;

      return Object.fromEntries(
        fields.map((field) => [
          field.name,
          parseDbfValue(buffer.subarray(base + field.offset, base + field.offset + field.length), field)
        ])
      );
    }
  };
};

const roundNumber = (value, precision) => {
  const factor = 10 ** precision;
  return Math.round(value * factor) / factor;
};

const signedRingArea = (ring) => {
  let area = 0;
  for (let index = 0, previous = ring.length - 1; index < ring.length; previous = index, index += 1) {
    const [x1, y1] = ring[previous];
    const [x2, y2] = ring[index];
    area += x1 * y2 - x2 * y1;
  }
  return area / 2;
};

const pointInRing = ([x, y], ring) => {
  let inside = false;
  for (let index = 0, previous = ring.length - 1; index < ring.length; previous = index, index += 1) {
    const [xi, yi] = ring[index];
    const [xj, yj] = ring[previous];
    const intersects = yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi || 1e-12) + xi;
    if (intersects) inside = !inside;
  }
  return inside;
};

const readParts = (buffer, start, coordinatePrecision) => {
  const numParts = buffer.readInt32LE(start + 36);
  const numPoints = buffer.readInt32LE(start + 40);
  const partsStart = start + 44;
  const pointsStart = partsStart + numParts * 4;
  const parts = Array.from({ length: numParts }, (_, index) => buffer.readInt32LE(partsStart + index * 4));
  const points = Array.from({ length: numPoints }, (_, index) => {
    const pointOffset = pointsStart + index * 16;
    return [roundNumber(buffer.readDoubleLE(pointOffset), coordinatePrecision), roundNumber(buffer.readDoubleLE(pointOffset + 8), coordinatePrecision)];
  });

  return parts
    .map((from, index) => points.slice(from, parts[index + 1] ?? points.length))
    .filter((part) => part.length > 1);
};

const parsePolyline = (buffer, start, coordinatePrecision) => {
  const lines = readParts(buffer, start, coordinatePrecision);
  if (lines.length === 1) {
    return { type: "LineString", coordinates: lines[0] };
  }
  return { type: "MultiLineString", coordinates: lines };
};

const parsePolygon = (buffer, start, coordinatePrecision) => {
  const rings = readParts(buffer, start, coordinatePrecision).filter((ring) => ring.length > 3);
  if (rings.length === 0) return null;

  const shells = [];
  const holes = [];
  for (const ring of rings) {
    if (signedRingArea(ring) < 0) {
      shells.push([ring]);
    } else {
      holes.push(ring);
    }
  }

  if (shells.length === 0) {
    const polygons = rings.map((ring) => [ring]);
    return polygons.length === 1
      ? { type: "Polygon", coordinates: polygons[0] }
      : { type: "MultiPolygon", coordinates: polygons };
  }

  for (const hole of holes) {
    const shell = shells.find((polygon) => pointInRing(hole[0], polygon[0]));
    if (shell) shell.push(hole);
  }

  return shells.length === 1
    ? { type: "Polygon", coordinates: shells[0] }
    : { type: "MultiPolygon", coordinates: shells };
};

const parsePoint = (buffer, start, coordinatePrecision) => ({
  type: "Point",
  coordinates: [roundNumber(buffer.readDoubleLE(start + 4), coordinatePrecision), roundNumber(buffer.readDoubleLE(start + 12), coordinatePrecision)]
});

const readShapeBounds = (buffer, start) => {
  const shapeType = buffer.readInt32LE(start);
  if (shapeType === 0) return null;
  if (shapeType === 1 || shapeType === 11 || shapeType === 21) {
    const lon = buffer.readDoubleLE(start + 4);
    const lat = buffer.readDoubleLE(start + 12);
    return [lon, lat, lon, lat];
  }
  if (shapeType === 3 || shapeType === 5 || shapeType === 13 || shapeType === 15 || shapeType === 23 || shapeType === 25) {
    return [
      buffer.readDoubleLE(start + 4),
      buffer.readDoubleLE(start + 12),
      buffer.readDoubleLE(start + 20),
      buffer.readDoubleLE(start + 28)
    ];
  }
  return null;
};

const parseShapeGeometry = (buffer, start, coordinatePrecision) => {
  const shapeType = buffer.readInt32LE(start);
  if (shapeType === 0) return null;
  if (shapeType === 1 || shapeType === 11 || shapeType === 21) return parsePoint(buffer, start, coordinatePrecision);
  if (shapeType === 3 || shapeType === 13 || shapeType === 23) return parsePolyline(buffer, start, coordinatePrecision);
  if (shapeType === 5 || shapeType === 15 || shapeType === 25) return parsePolygon(buffer, start, coordinatePrecision);
  return null;
};

export function readShapefileFromZip(root, { zipName, baseName, coordinatePrecision = 4, maxBuffer, filterRecord }) {
  const shp = readZipEntry(root, zipName, `${baseName}.shp`, maxBuffer);
  const dbf = parseDbf(readZipEntry(root, zipName, `${baseName}.dbf`, maxBuffer));
  const features = [];
  let offset = 100;

  for (let recordIndex = 0; offset < shp.length && recordIndex < dbf.records; recordIndex += 1) {
    const contentLength = shp.readInt32BE(offset + 4) * 2;
    const start = offset + 8;
    offset = start + contentLength;

    const properties = dbf.get(recordIndex);
    if (!properties) continue;

    const bbox = readShapeBounds(shp, start);
    const shapeType = shp.readInt32LE(start);
    if (filterRecord && !filterRecord({ properties, bbox, shapeType, index: recordIndex })) continue;

    const geometry = parseShapeGeometry(shp, start, coordinatePrecision);
    if (!geometry) continue;

    features.push({
      type: "Feature",
      id: recordIndex,
      properties,
      geometry
    });
  }

  return {
    type: "FeatureCollection",
    features
  };
}
