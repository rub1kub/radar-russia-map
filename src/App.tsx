import { useCallback, useDeferredValue, useEffect, useMemo, useRef, useState } from "react";
import "ol/ol.css";
import OlMap from "ol/Map";
import View from "ol/View";
import GeoJSON from "ol/format/GeoJSON";
import Feature from "ol/Feature";
import Point from "ol/geom/Point";
import TileLayer from "ol/layer/Tile";
import VectorLayer from "ol/layer/Vector";
import ClusterSource from "ol/source/Cluster";
import VectorSource from "ol/source/Vector";
import XYZ from "ol/source/XYZ";
import { Attribution, defaults as defaultControls, ScaleLine } from "ol/control";
import { createEmpty, extend } from "ol/extent";
import { unByKey } from "ol/Observable";
import { fromLonLat } from "ol/proj";
import Style from "ol/style/Style";
import Fill from "ol/style/Fill";
import Stroke from "ol/style/Stroke";
import Text from "ol/style/Text";
import CircleStyle from "ol/style/Circle";
import { Activity, Building2, Home, Info, Layers, MapPinned, Search } from "lucide-react";
import type { FeatureLike } from "ol/Feature";
import type { Geometry } from "ol/geom";

type GeoJsonFeatureCollection = {
  type: "FeatureCollection";
  features: Array<{
    type: "Feature";
    id?: string | number;
    properties: Record<string, unknown>;
    geometry: unknown;
  }>;
};

type PlaceRow = [
  id: string,
  name: string,
  asciiName: string,
  lat: number,
  lon: number,
  population: number | null,
  featureCode: string,
  typeLabel: string
];

type PlacesDataset = {
  fields: string[];
  rows: PlaceRow[];
};

type Dataset = {
  regions: GeoJsonFeatureCollection;
  districts: GeoJsonFeatureCollection;
  places: PlacesDataset;
};

type SelectedObject = {
  kind: "region" | "district" | "place";
  id: string;
  name: string;
  subtitle: string;
  details: Array<[string, string]>;
};

type SearchItem = {
  key: string;
  kind: SelectedObject["kind"];
  label: string;
  subtitle: string;
  searchText: string;
};

type RadarEvent = {
  id: string;
  first_seen_at: string;
  last_seen_at: string;
  status: string;
  signal_type: string;
  threat_type: string;
  severity: number;
  confidence: number;
  source_count: number;
  zone_id: string;
  zone_path: string[];
  place_name: string;
  zone_level: string;
  lat: number | null;
  lon: number | null;
  accuracy_m: number | null;
  target_count: number | null;
};

type RadarState = {
  generated_at: string;
  events: RadarEvent[];
  zone_counts: Record<string, { active: number; max_severity: number; last_active: string }>;
  active_events: number;
  active_zones: number;
};

type LayerState = {
  basemap: boolean;
  events: boolean;
  landCover: boolean;
  waterBodies: boolean;
  rivers: boolean;
  urbanAreas: boolean;
  roads: boolean;
  railways: boolean;
  regions: boolean;
  districts: boolean;
  places: boolean;
};

const WEB_MERCATOR_MAX_RESOLUTION = 156543.03392804097;
const RIVER_NETWORK_MAJOR_ZOOM = 4.8;
const RIVER_NETWORK_DETAIL_ZOOM = 7.6;
const URBAN_AREAS_ZOOM = 4.3;
const ROADS_ZOOM = 4.65;
const RAILWAYS_ZOOM = 4.8;
const DISTRICT_SELECTION_ZOOM = 5.4;
const PLACE_SELECTION_ZOOM = 7.2;
const BASEMAP_URL =
  import.meta.env.VITE_BASEMAP_URL || "https://{a-d}.basemaps.cartocdn.com/rastertiles/voyager_nolabels/{z}/{x}/{y}.png";
const BASEMAP_ATTRIBUTION = import.meta.env.VITE_BASEMAP_ATTRIBUTION || "© OpenStreetMap contributors, © CARTO";
const HILLSHADE_URL =
  import.meta.env.VITE_HILLSHADE_URL || "https://services.arcgisonline.com/ArcGIS/rest/services/Elevation/World_Hillshade/MapServer/tile/{z}/{y}/{x}";
const HILLSHADE_ATTRIBUTION = import.meta.env.VITE_HILLSHADE_ATTRIBUTION || "Tiles © Esri";
// Стартовый вид — европейская часть, где происходит почти вся обстановка.
const OVERVIEW_CENTER = fromLonLat([41, 53.5]);
const DESKTOP_OVERVIEW_ZOOM = 4.15;
const MOBILE_OVERVIEW_ZOOM = 3.3;
const MAX_SUGGESTIONS = 10;
const MOBILE_QUERY = "(max-width: 760px)";
const API_BASE = import.meta.env.VITE_API_BASE || "http://127.0.0.1:8000";
const STATE_POLL_MS = 10_000;

const SIGNAL_LABELS: Record<string, string> = {
  danger: "Опасность",
  alarm: "Тревога",
  detection: "Фиксация",
  intercept: "Работа ПВО",
  impact: "Взрыв",
  caution: "Меры безопасности",
  infra: "Инфраструктура",
  allclear: "Отбой",
  retracted: "Опровержение"
};

const THREAT_LABELS: Record<string, string> = {
  uav: "БПЛА",
  fpv: "FPV",
  rocket: "Ракета",
  kab: "КАБ/УАБ",
  bek: "БЭК",
  aviation: "Авиация",
  unknown: "Неизвестно"
};

function severityColor(severity: number, alpha: number): string {
  if (severity >= 8) return `rgba(214, 69, 69, ${alpha})`;
  if (severity >= 6) return `rgba(226, 124, 48, ${alpha})`;
  if (severity >= 4) return `rgba(228, 178, 62, ${alpha})`;
  return `rgba(140, 152, 146, ${alpha})`;
}

const LAYER_OPTIONS: Array<{ key: keyof LayerState; label: string; swatch: string }> = [
  { key: "basemap", label: "Подложка", swatch: "swatch-basemap" },
  { key: "regions", label: "Регионы", swatch: "swatch-region" },
  { key: "districts", label: "Районы", swatch: "swatch-district" },
  { key: "places", label: "Населенные пункты", swatch: "swatch-city" },
  { key: "roads", label: "Дороги", swatch: "swatch-road" },
  { key: "railways", label: "Железные дороги", swatch: "swatch-railway" },
  { key: "urbanAreas", label: "Контуры городов", swatch: "swatch-urban" },
  { key: "waterBodies", label: "Водоемы", swatch: "swatch-water" },
  { key: "rivers", label: "Реки", swatch: "swatch-river" },
  { key: "landCover", label: "Леса и болота", swatch: "swatch-land-cover" }
];

const numberFormat = new Intl.NumberFormat("ru-RU");
const emptySelected: SelectedObject = {
  kind: "region",
  id: "none",
  name: "Выберите объект на карте",
  subtitle: "Регион, район или населенный пункт",
  details: [["Слой", "Регионы, районы, дороги, рельеф, гидрография и населенные пункты"]]
};

function featureKey(feature: FeatureLike): string {
  return `${String(feature.get("kind") ?? "feature")}:${String(feature.get("id") ?? feature.get("name"))}`;
}

function asText(value: unknown, fallback = "—"): string {
  if (value === null || value === undefined || value === "") return fallback;
  return String(value);
}

function selectedFromFeature(feature: FeatureLike): SelectedObject {
  const kind = asText(feature.get("kind"), "region") as SelectedObject["kind"];
  const id = asText(feature.get("id"), asText(feature.get("name")));
  const name = asText(feature.get("name"));

  if (kind === "place") {
    const population = feature.get("population");
    return {
      kind,
      id,
      name,
      subtitle: asText(feature.get("typeLabel"), "Населенный пункт"),
      details: [
        ["Тип", asText(feature.get("typeLabel"), "Населенный пункт")],
        ["Население", typeof population === "number" ? numberFormat.format(population) : "нет данных"],
        ["Координаты", `${asText(feature.get("lat"))}, ${asText(feature.get("lon"))}`]
      ]
    };
  }

  if (kind === "district") {
    return {
      kind,
      id,
      name,
      subtitle: "Административный район / округ ADM2",
      details: [
        ["Тип", "Район / округ"],
        ["Название", name],
        ["ISO", asText(feature.get("iso"))]
      ]
    };
  }

  return {
    kind: "region",
    id,
    name,
    subtitle: "Субъект Российской Федерации",
    details: [
      ["Тип", "Регион"],
      ["Название", name],
      ["ISO", asText(feature.get("iso"))]
    ]
  };
}

function createRegionStyle(selectedKeyRef: React.MutableRefObject<string | null>) {
  return (feature: FeatureLike) => {
    const selected = selectedKeyRef.current === featureKey(feature);
    return [
      new Style({
        fill: new Fill({
          color: selected ? "rgba(228, 178, 93, 0.055)" : "rgba(255, 255, 255, 0.006)"
        })
      }),
      new Style({
        stroke: new Stroke({
          color: selected ? "rgba(255, 250, 230, 0.82)" : "rgba(248, 250, 242, 0.56)",
          width: selected ? 3.2 : 2
        })
      }),
      new Style({
        stroke: new Stroke({
          color: selected ? "rgba(125, 99, 48, 0.78)" : "rgba(91, 104, 100, 0.64)",
          width: selected ? 1.35 : 0.9
        })
      })
    ];
  };
}

function createDistrictStyle(selectedKeyRef: React.MutableRefObject<string | null>) {
  return (feature: FeatureLike) => {
    const selected = selectedKeyRef.current === featureKey(feature);
    return new Style({
      fill: new Fill({
        color: selected ? "rgba(228, 178, 93, 0.045)" : "rgba(255, 255, 255, 0.004)"
      }),
      stroke: new Stroke({
        color: selected ? "rgba(126, 98, 49, 0.62)" : "rgba(116, 124, 119, 0.16)",
        width: selected ? 1.25 : 0.35
      })
    });
  };
}

function resolutionToZoom(resolution: number): number {
  return Math.log2(WEB_MERCATOR_MAX_RESOLUTION / resolution);
}

function asNumber(value: unknown, fallback: number): number {
  const numberValue = Number(value);
  return Number.isFinite(numberValue) ? numberValue : fallback;
}

function createLandCoverStyle(feature: FeatureLike) {
  const kind = asText(feature.get("landCoverKind"), "");
  const color =
    kind === "forest"
      ? "rgba(54, 104, 68, 0.2)"
      : kind === "wetland"
        ? "rgba(60, 119, 124, 0.18)"
        : "rgba(128, 139, 122, 0.15)";
  const strokeColor =
    kind === "forest"
      ? "rgba(74, 132, 86, 0.14)"
      : kind === "wetland"
        ? "rgba(80, 148, 151, 0.14)"
        : "rgba(156, 164, 148, 0.12)";

  return new Style({
    fill: new Fill({ color }),
    stroke: new Stroke({ color: strokeColor, width: 0.45 })
  });
}

function createWaterBodyStyle(feature: FeatureLike, resolution: number) {
  const name = asText(feature.get("name"), "");
  const scalerank = asNumber(feature.get("scalerank"), 9);
  const minLabel = asNumber(feature.get("minLabel"), scalerank <= 4 ? 4.8 : 6.4);
  const showLabel = Boolean(name) && resolutionToZoom(resolution) >= minLabel;

  return new Style({
    fill: new Fill({
      color: scalerank <= 4 ? "rgba(61, 127, 143, 0.36)" : "rgba(54, 120, 136, 0.24)"
    }),
    stroke: new Stroke({
      color: "rgba(118, 184, 196, 0.34)",
      width: scalerank <= 4 ? 0.9 : 0.55
    }),
    text: showLabel
      ? new Text({
          text: name,
          font: "500 11px Inter, system-ui, sans-serif",
          fill: new Fill({ color: "rgba(164, 222, 228, 0.72)" }),
          stroke: new Stroke({ color: "rgba(8, 11, 11, 0.88)", width: 2.6 })
        })
      : undefined
  });
}

function createRiverStyle(feature: FeatureLike, resolution: number) {
  const name = asText(feature.get("name"), "");
  const scalerank = asNumber(feature.get("scalerank"), 9);
  const minLabel = asNumber(feature.get("minLabel"), scalerank <= 4 ? 5.2 : 7);
  const showLabel = Boolean(name) && resolutionToZoom(resolution) >= minLabel;
  const width = scalerank <= 2 ? 1.6 : scalerank <= 5 ? 1.05 : 0.62;

  return new Style({
    stroke: new Stroke({
      color: scalerank <= 4 ? "rgba(118, 191, 205, 0.52)" : "rgba(98, 171, 186, 0.34)",
      width
    }),
    text: showLabel
      ? new Text({
          text: name,
          placement: "line",
          overflow: true,
          font: "500 10px Inter, system-ui, sans-serif",
          fill: new Fill({ color: "rgba(161, 221, 230, 0.68)" }),
          stroke: new Stroke({ color: "rgba(8, 11, 11, 0.88)", width: 2.3 })
        })
      : undefined
  });
}

function createUrbanAreaStyle(feature: FeatureLike, resolution: number) {
  if (resolutionToZoom(resolution) < asNumber(feature.get("minZoom"), 6)) return undefined;

  const areaSqKm = asNumber(feature.get("areaSqKm"), 0);
  const alpha = areaSqKm >= 450 ? 0.32 : areaSqKm >= 120 ? 0.26 : areaSqKm >= 40 ? 0.2 : 0.16;
  return new Style({
    fill: new Fill({ color: `rgba(58, 68, 64, ${alpha})` }),
    stroke: new Stroke({
      color: areaSqKm >= 120 ? "rgba(64, 76, 72, 0.34)" : "rgba(72, 82, 78, 0.18)",
      width: areaSqKm >= 120 ? 0.8 : 0.45
    })
  });
}

function createRoadStyle(feature: FeatureLike, resolution: number) {
  const zoom = resolutionToZoom(resolution);
  if (zoom < asNumber(feature.get("minZoom"), 7)) return undefined;

  const type = asText(feature.get("type"), "");
  const expressway = Boolean(feature.get("expressway"));
  const name = asText(feature.get("name"), "");
  const scalerank = asNumber(feature.get("scalerank"), 8);
  const isMajor = expressway || scalerank <= 4 || /major|primary|trunk/i.test(type);
  const isSecondary = scalerank <= 6 || /secondary/i.test(type);
  if (!isMajor && !isSecondary && zoom < 7.1) return undefined;

  const width = isMajor ? (zoom < 6 ? 1.8 : 2.15) : isSecondary ? 1.05 : 0.7;
  const showLabel = Boolean(name) && zoom >= asNumber(feature.get("minLabel"), 8);

  return [
    new Style({
      stroke: new Stroke({
        color: isMajor ? "rgba(112, 88, 44, 0.56)" : "rgba(95, 83, 58, 0.42)",
        width: width + 1
      })
    }),
    new Style({
      stroke: new Stroke({
        color: isMajor ? "rgba(229, 174, 62, 0.78)" : "rgba(177, 151, 94, 0.56)",
        width
      }),
      text: showLabel
        ? new Text({
            text: name,
            placement: "line",
            overflow: true,
            font: "500 10px Inter, system-ui, sans-serif",
            fill: new Fill({ color: "rgba(83, 70, 42, 0.72)" }),
            stroke: new Stroke({ color: "rgba(244, 246, 238, 0.78)", width: 2.2 })
          })
        : undefined
    })
  ];
}

function createRailwayStyle(feature: FeatureLike, resolution: number) {
  const zoom = resolutionToZoom(resolution);
  const scalerank = asNumber(feature.get("scalerank"), 9);
  const category = asNumber(feature.get("category"), 2);
  const multiTrack = Boolean(feature.get("multiTrack"));
  const isMajor = scalerank <= 6 || category <= 1 || multiTrack;
  const minZoom = Math.max(asNumber(feature.get("minZoom"), isMajor ? 5.7 : 7.4), isMajor ? 5.55 : 7.35);
  if (zoom < minZoom) return undefined;

  const width = isMajor ? (zoom < 7 ? 1.05 : 1.25) : 0.75;

  return [
    new Style({
      stroke: new Stroke({
        color: isMajor ? "rgba(64, 68, 65, 0.58)" : "rgba(78, 82, 78, 0.42)",
        width
      })
    }),
    new Style({
      stroke: new Stroke({
        color: isMajor ? "rgba(248, 249, 242, 0.62)" : "rgba(248, 249, 242, 0.42)",
        width: Math.max(0.45, width - 0.35),
        lineDash: [2, 8]
      })
    })
  ];
}

const riverNetworkStyleCache = new Map<string, Style>();

function createRiverNetworkStyle(feature: FeatureLike, resolution: number) {
  const minZoom = asNumber(feature.get("minZoom"), 8);
  if (resolutionToZoom(resolution) < minZoom) return undefined;

  const widthClass = asNumber(feature.get("widthClass"), 1);
  const key = String(widthClass);
  const cached = riverNetworkStyleCache.get(key);
  if (cached) return cached;

  const width = widthClass >= 5 ? 1.45 : widthClass >= 4 ? 1.15 : widthClass >= 3 ? 0.86 : widthClass >= 2 ? 0.62 : 0.42;
  const alpha =
    widthClass >= 5 ? 0.62 : widthClass >= 4 ? 0.52 : widthClass >= 3 ? 0.42 : widthClass >= 2 ? 0.31 : 0.23;
  const style = new Style({
    stroke: new Stroke({
      color: `rgba(105, 188, 202, ${alpha})`,
      width
    })
  });
  riverNetworkStyleCache.set(key, style);
  return style;
}

function createEventStyle(feature: FeatureLike) {
  const severity = asNumber(feature.get("severity"), 4);
  const confidence = asNumber(feature.get("confidence"), 0.4);
  const fading = feature.get("status") === "fading";

  // Только цветная точка: цвет — опасность, размер и плотность — насколько
  // событие подтверждено. Ни цифр, ни подписей — названия уже есть на карте.
  const radius = 5 + confidence * 5;
  const alpha = (fading ? 0.34 : 0.8) * (0.5 + confidence * 0.5);

  return [
    new Style({
      image: new CircleStyle({
        radius: radius + 6,
        fill: new Fill({ color: severityColor(severity, alpha * 0.18) })
      })
    }),
    new Style({
      image: new CircleStyle({
        radius,
        fill: new Fill({ color: severityColor(severity, alpha) }),
        stroke: new Stroke({ color: "rgba(255, 255, 255, 0.9)", width: 1.6 })
      })
    })
  ];
}

function getClusterItems(feature: FeatureLike): Feature<Geometry>[] {
  const clustered = feature.get("features");
  if (Array.isArray(clustered)) return clustered as Feature<Geometry>[];
  return [feature as Feature<Geometry>];
}

function placeImportance(feature: Feature<Geometry>): number {
  const population = Number(feature.get("population") ?? 0);
  const featureCode = String(feature.get("featureCode") ?? "");
  const adminBonus =
    featureCode === "PPLC" ? 20_000_000 : featureCode === "PPLA" ? 5_000_000 : featureCode === "PPLA2" ? 1_000_000 : 0;
  const districtPenalty = featureCode === "PPLX" ? 750_000 : 0;
  return population + adminBonus - districtPenalty;
}

function shouldShowCityLabel(feature: Feature<Geometry>, zoom: number): boolean {
  const population = Number(feature.get("population") ?? 0);
  const featureCode = String(feature.get("featureCode") ?? "");
  const isAdminCenter = featureCode === "PPLC" || featureCode === "PPLA" || featureCode === "PPLA2";
  const isCityDistrict = featureCode === "PPLX";

  if (isCityDistrict && zoom < 9.5) return false;
  if (population >= 1_000_000) return zoom >= 2.2;
  if (population >= 700_000) return zoom >= 3.1;
  if (population >= 300_000) return zoom >= 4.1;
  if (population >= 100_000) return zoom >= 5.2;
  if (isAdminCenter && population >= 50_000) return zoom >= 6.1;
  if (population >= 20_000) return zoom >= 7.2;
  if (population >= 5_000) return zoom >= 8.4;
  return zoom >= 10.4;
}

function isPlaceLabelCandidate(feature: Feature<Geometry>): boolean {
  const population = Number(feature.get("population") ?? 0);
  return population >= 20_000;
}

function createPlaceStyle(selectedKeyRef: React.MutableRefObject<string | null>) {
  return (feature: FeatureLike, resolution: number) => {
    const places = getClusterItems(feature);
    const count = places.length;
    const selectedPlace = places.find((place) => selectedKeyRef.current === featureKey(place));
    const selected = Boolean(selectedPlace);
    const topPlace = places.reduce((best, place) => {
      return placeImportance(place) > placeImportance(best) ? place : best;
    }, selectedPlace ?? places[0]);
    const zoom = resolutionToZoom(resolution);
    const labelFeature = selectedPlace ?? topPlace;
    const showPlaceLabel = selected || (!isPlaceLabelCandidate(labelFeature) && count === 1 && shouldShowCityLabel(labelFeature, zoom));
    if (!showPlaceLabel) return undefined;

    return new Style({
      text: new Text({
        text: String(labelFeature.get("name") ?? ""),
        font: selected ? "700 12px Inter, system-ui, sans-serif" : "500 11px Inter, system-ui, sans-serif",
        fill: new Fill({ color: selected ? "rgba(36, 38, 34, 0.94)" : "rgba(68, 73, 69, 0.76)" }),
        stroke: new Stroke({ color: selected ? "rgba(255, 238, 190, 0.86)" : "rgba(246, 248, 240, 0.74)", width: selected ? 3 : 2.2 })
      })
    });
  };
}

function createPlaceLabelStyle(selectedKeyRef: React.MutableRefObject<string | null>) {
  return (feature: FeatureLike, resolution: number) => {
    const typedFeature = feature as Feature<Geometry>;
    const selected = selectedKeyRef.current === featureKey(feature);
    const zoom = resolutionToZoom(resolution);
    if (!selected && !shouldShowCityLabel(typedFeature, zoom)) return undefined;

    const population = Number(feature.get("population") ?? 0);
    const isMajor = population >= 700_000 || selected;
    const fontSize = zoom < 3.2 ? 10 : isMajor ? 11.5 : 10.5;
    const strokeWidth = zoom < 3.2 ? 2.1 : isMajor ? 2.7 : 2.2;

    return new Style({
      text: new Text({
        text: String(feature.get("name") ?? ""),
        font: `${isMajor && zoom >= 3.2 ? 600 : 500} ${fontSize}px Inter, system-ui, sans-serif`,
        fill: new Fill({ color: isMajor ? "rgba(43, 47, 44, 0.9)" : "rgba(70, 76, 72, 0.74)" }),
        stroke: new Stroke({ color: "rgba(247, 249, 242, 0.78)", width: strokeWidth })
      })
    });
  };
}

async function loadDataset(): Promise<Dataset> {
  const [regions, districts, places] = await Promise.all([
    fetch("/data/regions.json").then((response) => response.json()),
    fetch("/data/districts.json").then((response) => response.json()),
    fetch("/data/places.json").then((response) => response.json())
  ]);

  return { regions, districts, places };
}

function fitFeature(map: OlMap, feature: FeatureLike, maxZoom: number) {
  const geometry = feature.getGeometry();
  if (!geometry) return;
  map.getView().fit(geometry.getExtent(), {
    padding: getMapFitPadding(),
    duration: 420,
    maxZoom
  });
}

function fitFeatures(map: OlMap, features: Feature<Geometry>[], maxZoom: number) {
  const extent = createEmpty();
  for (const feature of features) {
    const geometry = feature.getGeometry();
    if (geometry) extend(extent, geometry.getExtent());
  }
  map.getView().fit(extent, {
    padding: getMapFitPadding(),
    duration: 360,
    maxZoom
  });
}

function getMapFitPadding(): [number, number, number, number] {
  if (window.matchMedia(MOBILE_QUERY).matches) {
    return [32, 24, 72, 24];
  }

  return [54, 54, 72, 54];
}

function getOverviewZoom(): number {
  return window.matchMedia(MOBILE_QUERY).matches ? MOBILE_OVERVIEW_ZOOM : DESKTOP_OVERVIEW_ZOOM;
}

function setOverviewView(map: OlMap, duration: number) {
  const view = map.getView();
  if (duration > 0) {
    view.animate({ center: OVERVIEW_CENTER, zoom: getOverviewZoom(), duration });
    return;
  }

  view.setCenter(OVERVIEW_CENTER);
  view.setZoom(getOverviewZoom());
}

function createSearchCatalog(data: Dataset | null): SearchItem[] {
  if (!data) return [];

  const regionItems = data.regions.features.map((feature) => ({
    key: `region:${String(feature.properties.id ?? feature.properties.name)}`,
    kind: "region" as const,
    label: String(feature.properties.name ?? "Регион"),
    subtitle: "Регион",
    searchText: `${String(feature.properties.name ?? "")} ${String(feature.properties.iso ?? "")}`.toLowerCase()
  }));

  const districtItems = data.districts.features.map((feature) => ({
    key: `district:${String(feature.properties.id ?? feature.properties.name)}`,
    kind: "district" as const,
    label: String(feature.properties.name ?? "Район"),
    subtitle: "Район",
    searchText: `${String(feature.properties.name ?? "")} ${String(feature.properties.iso ?? "")}`.toLowerCase()
  }));

  const placeItems = data.places.rows.map(([id, name, asciiName, , , , , typeLabel]) => ({
    key: `place:${id}`,
    kind: "place" as const,
    label: name,
    subtitle: typeLabel,
    searchText: `${name} ${asciiName} ${typeLabel}`.toLowerCase()
  }));

  return [...regionItems, ...placeItems, ...districtItems];
}

export default function App() {
  const mapNodeRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<OlMap | null>(null);
  const basemapLayerRef = useRef<TileLayer<XYZ> | null>(null);
  const hillshadeLayerRef = useRef<TileLayer<XYZ> | null>(null);
  const landCoverLayerRef = useRef<VectorLayer<VectorSource<Feature<Geometry>>> | null>(null);
  const waterBodyLayerRef = useRef<VectorLayer<VectorSource<Feature<Geometry>>> | null>(null);
  const riverLayerRef = useRef<VectorLayer<VectorSource<Feature<Geometry>>> | null>(null);
  const riverNetworkMajorLayerRef = useRef<VectorLayer<VectorSource<Feature<Geometry>>> | null>(null);
  const riverNetworkDetailLayerRef = useRef<VectorLayer<VectorSource<Feature<Geometry>>> | null>(null);
  const urbanAreaLayerRef = useRef<VectorLayer<VectorSource<Feature<Geometry>>> | null>(null);
  const roadLayerRef = useRef<VectorLayer<VectorSource<Feature<Geometry>>> | null>(null);
  const railwayLayerRef = useRef<VectorLayer<VectorSource<Feature<Geometry>>> | null>(null);
  const landCoverLoadedRef = useRef(false);
  const waterBodiesLoadedRef = useRef(false);
  const riversLoadedRef = useRef(false);
  const riverNetworkMajorLoadedRef = useRef(false);
  const riverNetworkDetailLoadedRef = useRef(false);
  const urbanAreasLoadedRef = useRef(false);
  const roadsLoadedRef = useRef(false);
  const railwaysLoadedRef = useRef(false);
  const regionLayerRef = useRef<VectorLayer<VectorSource<Feature<Geometry>>> | null>(null);
  const districtLayerRef = useRef<VectorLayer<VectorSource<Feature<Geometry>>> | null>(null);
  const placeLayerRef = useRef<VectorLayer<ClusterSource<Feature<Geometry>>> | null>(null);
  const placeLabelLayerRef = useRef<VectorLayer<VectorSource<Feature<Geometry>>> | null>(null);
  const eventLayerRef = useRef<VectorLayer<VectorSource<Feature<Geometry>>> | null>(null);
  const eventSourceRef = useRef<VectorSource<Feature<Geometry>>>(new VectorSource());
  const selectedKeyRef = useRef<string | null>(null);
  const layersRef = useRef<LayerState | null>(null);
  const loadLazyLayersRef = useRef<(() => void) | null>(null);
  const featureIndexRef = useRef<globalThis.Map<string, Feature<Geometry>>>(new globalThis.Map());

  const [dataset, setDataset] = useState<Dataset | null>(null);
  const [selected, setSelected] = useState<SelectedObject>(emptySelected);
  const [radarState, setRadarState] = useState<RadarState | null>(null);
  const [apiOnline, setApiOnline] = useState<boolean | null>(null);
  const [layers, setLayers] = useState<LayerState>({
    basemap: true,
    events: true,
    landCover: false,
    waterBodies: false,
    rivers: false,
    urbanAreas: true,
    roads: true,
    railways: true,
    regions: true,
    districts: true,
    places: true
  });
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const deferredQuery = useDeferredValue(query);

  const catalog = useMemo(() => createSearchCatalog(dataset), [dataset]);

  const suggestions = useMemo(() => {
    const normalized = deferredQuery.trim().toLowerCase();
    if (normalized.length < 2) return [];
    return catalog
      .filter((item) => item.searchText.includes(normalized))
      .slice(0, MAX_SUGGESTIONS);
  }, [catalog, deferredQuery]);

  const applySelectedFeature = useCallback((feature: FeatureLike | null) => {
    selectedKeyRef.current = feature ? featureKey(feature) : null;
    setSelected(feature ? selectedFromFeature(feature) : emptySelected);
    regionLayerRef.current?.changed();
    districtLayerRef.current?.changed();
    placeLayerRef.current?.changed();
    placeLabelLayerRef.current?.changed();
  }, []);

  useEffect(() => {
    let active = true;
    setLoading(true);
    loadDataset()
      .then((data) => {
        if (!active) return;
        setDataset(data);
        setLoading(false);
      })
      .catch((reason: unknown) => {
        if (!active) return;
        setError(reason instanceof Error ? reason.message : "Не удалось загрузить данные карты");
        setLoading(false);
      });

    return () => {
      active = false;
    };
  }, []);

  // Обстановка из API конвейера. Карта работает и без него — просто без событий.
  useEffect(() => {
    let active = true;

    const pull = async () => {
      try {
        const response = await fetch(`${API_BASE}/api/v1/state`);
        if (!response.ok) throw new Error(String(response.status));
        const payload = (await response.json()) as RadarState;
        if (!active) return;
        setRadarState(payload);
        setApiOnline(true);
      } catch {
        if (active) setApiOnline(false);
      }
    };

    void pull();
    const timer = window.setInterval(pull, STATE_POLL_MS);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, []);

  // Синхронизация точек событий с источником слоя.
  useEffect(() => {
    const source = eventSourceRef.current;
    source.clear();
    if (!radarState) return;

    const features = radarState.events
      .filter((event) => typeof event.lat === "number" && typeof event.lon === "number")
      .map((event) => new Feature({
        geometry: new Point(fromLonLat([event.lon as number, event.lat as number])),
        kind: "event",
        id: event.id,
        placeName: event.place_name,
        severity: event.severity,
        confidence: event.confidence,
        sourceCount: event.source_count,
        status: event.status,
        signalType: event.signal_type,
        threatType: event.threat_type
      }));
    source.addFeatures(features);
  }, [radarState]);

  useEffect(() => {
    if (!dataset || !mapNodeRef.current || mapRef.current) return;
    layersRef.current = layers;

    const geoJson = new GeoJSON();
    const regionFeatures = geoJson.readFeatures(dataset.regions, {
      dataProjection: "EPSG:4326",
      featureProjection: "EPSG:3857"
    }) as Feature<Geometry>[];
    regionFeatures.forEach((feature) => {
      feature.set("kind", "region");
      featureIndexRef.current.set(featureKey(feature), feature);
    });

    const districtFeatures = geoJson.readFeatures(dataset.districts, {
      dataProjection: "EPSG:4326",
      featureProjection: "EPSG:3857"
    }) as Feature<Geometry>[];
    districtFeatures.forEach((feature) => {
      feature.set("kind", "district");
      featureIndexRef.current.set(featureKey(feature), feature);
    });

    const placeFeatures = dataset.places.rows.map(([id, name, asciiName, lat, lon, population, featureCode, typeLabel]) => {
      const feature = new Feature({
        geometry: new Point(fromLonLat([lon, lat])),
        kind: "place",
        id,
        name,
        asciiName,
        lat,
        lon,
        population,
        featureCode,
        typeLabel
      });
      featureIndexRef.current.set(featureKey(feature), feature);
      return feature;
    });

    const landCoverSource = new VectorSource<Feature<Geometry>>();
    const waterBodySource = new VectorSource<Feature<Geometry>>();
    const riverSource = new VectorSource<Feature<Geometry>>();
    const riverNetworkMajorSource = new VectorSource<Feature<Geometry>>();
    const riverNetworkDetailSource = new VectorSource<Feature<Geometry>>();
    const urbanAreaSource = new VectorSource<Feature<Geometry>>();
    const roadSource = new VectorSource<Feature<Geometry>>();
    const railwaySource = new VectorSource<Feature<Geometry>>();
    const regionSource = new VectorSource({ features: regionFeatures });
    const districtSource = new VectorSource({ features: districtFeatures });
    const placeSource = new VectorSource({ features: placeFeatures });
    const placeLabelSource = new VectorSource({ features: placeFeatures.filter(isPlaceLabelCandidate) });
    const placeClusterSource = new ClusterSource({
      distance: 22,
      minDistance: 4,
      source: placeSource
    });

    const basemapLayer = new TileLayer({
      source: new XYZ({
        url: BASEMAP_URL,
        attributions: BASEMAP_ATTRIBUTION,
        crossOrigin: "anonymous",
        maxZoom: 20
      }),
      visible: layers.basemap,
      opacity: 0.9,
      zIndex: 0
    });
    const hillshadeLayer = new TileLayer({
      source: new XYZ({
        url: HILLSHADE_URL,
        attributions: HILLSHADE_ATTRIBUTION,
        crossOrigin: "anonymous",
        maxZoom: 13
      }),
      visible: layers.basemap,
      opacity: 0.24,
      zIndex: 1
    });
    const landCoverLayer = new VectorLayer({
      source: landCoverSource,
      visible: layers.landCover,
      zIndex: 2,
      style: createLandCoverStyle
    });
    const waterBodyLayer = new VectorLayer({
      source: waterBodySource,
      visible: layers.waterBodies,
      zIndex: 5,
      style: createWaterBodyStyle
    });
    const riverLayer = new VectorLayer({
      source: riverSource,
      visible: layers.rivers,
      zIndex: 8,
      minZoom: 2.6,
      style: createRiverStyle
    });
    const riverNetworkMajorLayer = new VectorLayer({
      source: riverNetworkMajorSource,
      visible: layers.rivers,
      zIndex: 6,
      minZoom: 3.8,
      style: createRiverNetworkStyle
    });
    const riverNetworkDetailLayer = new VectorLayer({
      source: riverNetworkDetailSource,
      visible: layers.rivers,
      zIndex: 7,
      minZoom: 7.4,
      style: createRiverNetworkStyle
    });
    const urbanAreaLayer = new VectorLayer({
      source: urbanAreaSource,
      visible: layers.urbanAreas,
      zIndex: 8,
      minZoom: 4.3,
      style: createUrbanAreaStyle
    });
    const roadLayer = new VectorLayer({
      source: roadSource,
      visible: layers.roads,
      zIndex: 24,
      minZoom: 4.6,
      style: createRoadStyle
    });
    const railwayLayer = new VectorLayer({
      source: railwaySource,
      visible: layers.railways,
      zIndex: 23,
      minZoom: 4.75,
      style: createRailwayStyle
    });
    const regionLayer = new VectorLayer({
      source: regionSource,
      visible: layers.regions,
      zIndex: 28,
      style: createRegionStyle(selectedKeyRef)
    });
    const districtLayer = new VectorLayer({
      source: districtSource,
      visible: layers.districts,
      zIndex: 20,
      minZoom: 4.15,
      style: createDistrictStyle(selectedKeyRef)
    });
    const placeLayer = new VectorLayer({
      source: placeClusterSource,
      visible: layers.places,
      zIndex: 30,
      minZoom: 2.15,
      style: createPlaceStyle(selectedKeyRef)
    });
    const placeLabelLayer = new VectorLayer({
      source: placeLabelSource,
      visible: layers.places,
      zIndex: 31,
      minZoom: 2.15,
      declutter: true,
      style: createPlaceLabelStyle(selectedKeyRef)
    });

    const eventLayer = new VectorLayer({
      source: eventSourceRef.current,
      visible: layers.events,
      zIndex: 40,
      style: createEventStyle
    });

    basemapLayerRef.current = basemapLayer;
    eventLayerRef.current = eventLayer;
    hillshadeLayerRef.current = hillshadeLayer;
    landCoverLayerRef.current = landCoverLayer;
    waterBodyLayerRef.current = waterBodyLayer;
    riverLayerRef.current = riverLayer;
    riverNetworkMajorLayerRef.current = riverNetworkMajorLayer;
    riverNetworkDetailLayerRef.current = riverNetworkDetailLayer;
    urbanAreaLayerRef.current = urbanAreaLayer;
    roadLayerRef.current = roadLayer;
    railwayLayerRef.current = railwayLayer;
    regionLayerRef.current = regionLayer;
    districtLayerRef.current = districtLayer;
    placeLayerRef.current = placeLayer;
    placeLabelLayerRef.current = placeLabelLayer;

    const map = new OlMap({
      target: mapNodeRef.current,
      controls: defaultControls({ attribution: false, zoom: false }).extend([
        new Attribution({ collapsed: true, collapsible: true }),
        new ScaleLine({ minWidth: 90 })
      ]),
      layers: [
        basemapLayer,
        hillshadeLayer,
        landCoverLayer,
        waterBodyLayer,
        riverNetworkMajorLayer,
        riverNetworkDetailLayer,
        riverLayer,
        urbanAreaLayer,
        roadLayer,
        railwayLayer,
        regionLayer,
        districtLayer,
        placeLayer,
        placeLabelLayer,
        eventLayer
      ],
      view: new View({
        center: OVERVIEW_CENTER,
        zoom: getOverviewZoom(),
        minZoom: 1.75,
        maxZoom: 9.8
      })
    });

    setOverviewView(map, 0);
    let disposed = false;

    const loadVectorLayer = async (
      url: string,
      source: VectorSource<Feature<Geometry>>,
      loadedRef: React.MutableRefObject<boolean>,
      layerName: string
    ) => {
      if (loadedRef.current) return;
      loadedRef.current = true;
      try {
        const response = await fetch(url);
        if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
        const data = (await response.json()) as GeoJsonFeatureCollection;
        if (disposed) return;
        const features = geoJson.readFeatures(data, {
          dataProjection: "EPSG:4326",
          featureProjection: "EPSG:3857"
        }) as Feature<Geometry>[];
        source.addFeatures(features);
      } catch (reason: unknown) {
        loadedRef.current = false;
        if (!disposed) {
          setError(reason instanceof Error ? `Не удалось загрузить слой ${layerName}: ${reason.message}` : `Не удалось загрузить слой ${layerName}`);
        }
      }
    };

    const maybeLoadLazyLayers = () => {
      const zoom = map.getView().getZoom() ?? 0;
      const currentLayers = layersRef.current ?? layers;
      if (currentLayers.landCover) {
        void loadVectorLayer("/data/land-cover.json", landCoverSource, landCoverLoadedRef, "природных зон");
      }
      if (currentLayers.waterBodies) {
        void loadVectorLayer("/data/water-bodies.json", waterBodySource, waterBodiesLoadedRef, "водоемов");
      }
      if (currentLayers.rivers) {
        void loadVectorLayer("/data/rivers.json", riverSource, riversLoadedRef, "рек");
      }
      if (currentLayers.rivers && zoom >= RIVER_NETWORK_MAJOR_ZOOM) {
        void loadVectorLayer("/data/river-network-major.json", riverNetworkMajorSource, riverNetworkMajorLoadedRef, "рек");
      }
      if (currentLayers.rivers && zoom >= RIVER_NETWORK_DETAIL_ZOOM) {
        void loadVectorLayer("/data/river-network-detail.json", riverNetworkDetailSource, riverNetworkDetailLoadedRef, "рек");
      }
      if (currentLayers.urbanAreas && zoom >= URBAN_AREAS_ZOOM) {
        void loadVectorLayer("/data/urban-areas.json", urbanAreaSource, urbanAreasLoadedRef, "городских контуров");
      }
      if (currentLayers.roads && zoom >= ROADS_ZOOM) {
        void loadVectorLayer("/data/roads.json", roadSource, roadsLoadedRef, "дорог");
      }
      if (currentLayers.railways && zoom >= RAILWAYS_ZOOM) {
        void loadVectorLayer("/data/railways.json", railwaySource, railwaysLoadedRef, "железных дорог");
      }
    };

    loadLazyLayersRef.current = maybeLoadLazyLayers;
    const viewResolutionKey = map.getView().on("change:resolution", maybeLoadLazyLayers);
    maybeLoadLazyLayers();

    map.on("pointermove", (event) => {
      const hit = map.hasFeatureAtPixel(event.pixel, {
        hitTolerance: 5,
        layerFilter: (layer) => layer === regionLayer || layer === districtLayer || layer === placeLayer
      });
      const target = map.getTargetElement();
      if (target) target.style.cursor = hit ? "pointer" : "";
    });

    map.on("singleclick", (event) => {
      const zoom = map.getView().getZoom() ?? 0;
      let regionFeature: FeatureLike | null = null;
      let districtFeature: FeatureLike | null = null;
      let placeFeature: FeatureLike | null = null;
      let clusterFeatures: Feature<Geometry>[] | null = null;

      map.forEachFeatureAtPixel(
        event.pixel,
        (feature, layer) => {
          if (layer === placeLayer) {
            const places = getClusterItems(feature);
            if (places.length > 1) {
              clusterFeatures = places;
            } else {
              placeFeature = places[0] ?? feature;
            }
            return false;
          }

          if (layer === districtLayer && !districtFeature) {
            districtFeature = feature;
            return false;
          }

          if (layer === regionLayer && !regionFeature) {
            regionFeature = feature;
          }
          return false;
        },
        {
          hitTolerance: 8,
          layerFilter: (layer) => layer === placeLayer || layer === districtLayer || layer === regionLayer
        }
      );

      if (zoom >= PLACE_SELECTION_ZOOM && clusterFeatures) {
        fitFeatures(map, clusterFeatures, 8.4);
        return;
      }

      if (zoom >= PLACE_SELECTION_ZOOM && placeFeature) {
        applySelectedFeature(placeFeature);
        return;
      }

      applySelectedFeature(zoom >= DISTRICT_SELECTION_ZOOM ? districtFeature ?? regionFeature : regionFeature ?? districtFeature);
    });

    mapRef.current = map;

    return () => {
      disposed = true;
      unByKey(viewResolutionKey);
      map.setTarget(undefined);
      mapRef.current = null;
      basemapLayerRef.current = null;
      hillshadeLayerRef.current = null;
      landCoverLayerRef.current = null;
      waterBodyLayerRef.current = null;
      riverLayerRef.current = null;
      riverNetworkMajorLayerRef.current = null;
      riverNetworkDetailLayerRef.current = null;
      urbanAreaLayerRef.current = null;
      roadLayerRef.current = null;
      railwayLayerRef.current = null;
      landCoverLoadedRef.current = false;
      waterBodiesLoadedRef.current = false;
      riversLoadedRef.current = false;
      riverNetworkMajorLoadedRef.current = false;
      riverNetworkDetailLoadedRef.current = false;
      urbanAreasLoadedRef.current = false;
      roadsLoadedRef.current = false;
      railwaysLoadedRef.current = false;
      loadLazyLayersRef.current = null;
      eventLayerRef.current = null;
      layersRef.current = null;
      regionLayerRef.current = null;
      districtLayerRef.current = null;
      placeLayerRef.current = null;
      placeLabelLayerRef.current = null;
      featureIndexRef.current.clear();
    };
  }, [applySelectedFeature, dataset]);

  useEffect(() => {
    layersRef.current = layers;
    eventLayerRef.current?.setVisible(layers.events);
    basemapLayerRef.current?.setVisible(layers.basemap);
    hillshadeLayerRef.current?.setVisible(layers.basemap);
    landCoverLayerRef.current?.setVisible(layers.landCover);
    waterBodyLayerRef.current?.setVisible(layers.waterBodies);
    riverLayerRef.current?.setVisible(layers.rivers);
    riverNetworkMajorLayerRef.current?.setVisible(layers.rivers);
    riverNetworkDetailLayerRef.current?.setVisible(layers.rivers);
    urbanAreaLayerRef.current?.setVisible(layers.urbanAreas);
    roadLayerRef.current?.setVisible(layers.roads);
    railwayLayerRef.current?.setVisible(layers.railways);
    regionLayerRef.current?.setVisible(layers.regions);
    districtLayerRef.current?.setVisible(layers.districts);
    placeLayerRef.current?.setVisible(layers.places);
    placeLabelLayerRef.current?.setVisible(layers.places);
    loadLazyLayersRef.current?.();
  }, [layers]);

  const selectSearchItem = useCallback(
    (item: SearchItem) => {
      const feature = featureIndexRef.current.get(item.key);
      const map = mapRef.current;
      if (!feature || !map) return;

      applySelectedFeature(feature);
      if (item.kind === "place") {
        const geometry = feature.getGeometry();
        if (geometry instanceof Point) {
          map.getView().animate({
            center: geometry.getCoordinates(),
            zoom: Math.max(map.getView().getZoom() ?? 5, 8.8),
            duration: 420
          });
        }
      } else {
        fitFeature(map, feature, item.kind === "region" ? 5.2 : 7.4);
      }
      setQuery(item.label);
    },
    [applySelectedFeature]
  );

  const resetMap = useCallback(() => {
    const map = mapRef.current;
    if (!map) return;
    setOverviewView(map, 420);
    applySelectedFeature(null);
    setQuery("");
  }, [applySelectedFeature]);

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand">
            <MapPinned size={22} aria-hidden="true" />
          <div>
            <h1>Карта обстановки</h1>
            <p>По открытым источникам</p>
          </div>
        </div>
      </header>

      <main className="workspace">
        <aside className="sidebar" aria-label="Панель управления картой">
          <section className="tool-section">
            <label className="search-box" htmlFor="map-search">
              <Search size={18} aria-hidden="true" />
              <input
                id="map-search"
                type="search"
                value={query}
                placeholder="Поиск региона, района или населенного пункта"
                onChange={(event) => setQuery(event.target.value)}
              />
            </label>
            {suggestions.length > 0 ? (
              <div className="suggestions" role="listbox" aria-label="Найденные объекты">
                {suggestions.map((item) => (
                  <button key={item.key} type="button" onClick={() => selectSearchItem(item)}>
                    <span>{item.label}</span>
                    <small>{item.subtitle}</small>
                  </button>
                ))}
              </div>
            ) : null}
          </section>

          <section className="tool-section">
            <div className="severity-legend">
              <span><i style={{ background: severityColor(9, 0.95) }} aria-hidden="true" />Ракета, взрыв, ПВО</span>
              <span><i style={{ background: severityColor(7, 0.95) }} aria-hidden="true" />Тревога</span>
              <span><i style={{ background: severityColor(5, 0.95) }} aria-hidden="true" />Опасность</span>
            </div>
            <p className="legend-note">
              Чем ярче и крупнее точка, тем больше независимых источников подтвердили событие.
            </p>
            <button className="ghost-button" type="button" onClick={resetMap}>
              <Home size={17} aria-hidden="true" />
              <span>Сбросить вид</span>
            </button>
          </section>

          <details className="extra-layers">
            <summary>
              <Layers size={16} aria-hidden="true" />
              <span>Слои карты</span>
            </summary>
            <div className="layer-list">
              {LAYER_OPTIONS.map(({ key, label, swatch }) => (
                <label key={key}>
                  <input
                    type="checkbox"
                    checked={layers[key]}
                    onChange={(event) =>
                      setLayers((current) => ({ ...current, [key]: event.target.checked }))
                    }
                  />
                  <span className={`swatch ${swatch}`} aria-hidden="true" />
                  <span>{label}</span>
                </label>
              ))}
            </div>
          </details>
        </aside>

        <section className="map-panel" aria-label="Интерактивная карта">
          <div className="map-surface" ref={mapNodeRef} />
          {loading ? <div className="map-loader">Загрузка карты…</div> : null}
          {error ? <div className="map-error">{error}</div> : null}
        </section>

        <aside className="details-panel" aria-label="Обстановка">
          <div className="feed-top">
            <h2>Что происходит</h2>
            <span className={`live-dot ${apiOnline ? "is-live" : "is-off"}`} title={apiOnline ? "Данные обновляются" : "Нет связи"} aria-hidden="true" />
          </div>

          {selected.id !== "none" ? (
            <button className="selected-card" type="button" onClick={() => applySelectedFeature(null)}>
              <Building2 size={16} aria-hidden="true" />
              <span>{selected.name}</span>
              <small>{selected.kind === "place" ? "населенный пункт" : selected.kind === "district" ? "район" : "регион"}</small>
            </button>
          ) : null}

          {apiOnline === false ? (
            <p className="feed-empty">Нет связи с сервером. Обновите страницу.</p>
          ) : radarState ? (
            <>
              <p className="feed-summary">
                <strong>{radarState.active_events}</strong> активных сообщений
              </p>
              <ul className="event-feed">
                {radarState.events.slice(0, 40).map((event) => (
                  <li key={event.id} className={event.status === "fading" ? "is-fading" : undefined}>
                    <span className="event-dot" style={{ background: severityColor(event.severity, 0.95) }} aria-hidden="true" />
                    <div className="event-body">
                      <p className="event-title">{event.place_name}</p>
                      <p className="event-meta">
                        {SIGNAL_LABELS[event.signal_type] ?? event.signal_type}
                        {event.threat_type !== "unknown" ? ` · ${THREAT_LABELS[event.threat_type] ?? event.threat_type}` : ""}
                      </p>
                    </div>
                    <span className="event-time">{event.last_seen_at.slice(11, 16)}</span>
                  </li>
                ))}
              </ul>
            </>
          ) : (
            <p className="feed-empty">Загрузка…</p>
          )}
        </aside>
      </main>
    </div>
  );
}
