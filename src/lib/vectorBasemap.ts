import type VectorTileLayer from "ol/layer/VectorTile";
import { applyStyle } from "ol-mapbox-style";
import { OPENFREE_ATTRIBUTION } from "./basemaps";

type GlLayer = {
  id: string;
  type: string;
  source?: string;
  paint?: Record<string, unknown>;
};

type GlStyle = {
  version: number;
  sources: Record<string, unknown>;
  layers: GlLayer[];
  [key: string]: unknown;
};

function isGlStyle(value: unknown): value is GlStyle {
  if (!value || typeof value !== "object") return false;
  const style = value as Partial<GlStyle>;
  return style.version === 8 && !!style.sources && Array.isArray(style.layers);
}

/**
 * Keep the provider's cartography but remove raster relief and every baked
 * label/icon. Russian place labels are rendered by the project's own layers.
 */
export function labelFreeVectorStyle(value: unknown): GlStyle {
  if (!isGlStyle(value)) throw new Error("Некорректный стиль подложки");

  const layers = value.layers.filter(
    (layer) => layer.source === "openmaptiles" && layer.type !== "symbol"
  );
  if (!layers.length || !value.sources.openmaptiles) {
    throw new Error("В стиле подложки нет openmaptiles-слоёв");
  }

  return { ...value, layers };
}

function backgroundColor(style: GlStyle): string | undefined {
  const layer = style.layers.find((item) => item.type === "background");
  const color = layer?.paint?.["background-color"];
  return typeof color === "string" ? color : undefined;
}

/** Load one of OpenFreeMap's maintained styles into an OpenLayers layer. */
export async function applyLabelFreeVectorBasemap(
  layer: VectorTileLayer,
  styleUrl: string
): Promise<void> {
  const response = await fetch(styleUrl);
  if (!response.ok) {
    throw new Error(`Не удалось загрузить стиль подложки: ${response.status}`);
  }

  const raw = await response.json() as unknown;
  const color = isGlStyle(raw) ? backgroundColor(raw) : undefined;
  const style = labelFreeVectorStyle(raw);
  if (color) layer.setBackground(color);

  await applyStyle(layer, style, { source: "openmaptiles" });
  layer.getSource()?.setAttributions(OPENFREE_ATTRIBUTION);
}
