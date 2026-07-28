import { useCallback, useDeferredValue, useEffect, useMemo, useRef, useState } from "react";
import "ol/ol.css";
import OlMap from "ol/Map";
import View from "ol/View";
import GeoJSON from "ol/format/GeoJSON";
import Feature from "ol/Feature";
import Point from "ol/geom/Point";
import TileLayer from "ol/layer/Tile";
import VectorLayer from "ol/layer/Vector";
import VectorSource from "ol/source/Vector";
import XYZ from "ol/source/XYZ";
import { Attribution, defaults as defaultControls, ScaleLine } from "ol/control";
import { containsCoordinate, createEmpty, extend, getCenter } from "ol/extent";
import { unByKey } from "ol/Observable";
import { fromLonLat } from "ol/proj";
import Style from "ol/style/Style";
import Fill from "ol/style/Fill";
import Stroke from "ol/style/Stroke";
import Text from "ol/style/Text";
import Icon from "ol/style/Icon";
import {
  BarChart3,
  Building2,
  ChevronLeft,
  ChevronRight,
  Home,
  Layers,
  MapPinned,
  Search,
  SlidersHorizontal
} from "lucide-react";
import { api, API_BASE } from "./lib/api";
import type { RadarEvent, RadarState, SearchItem, ZoneCount } from "./lib/api";
import {
  formatAge,
  formatDayTime,
  formatDuration,
  formatMoment,
  numberFormat,
  plural,
  severityColor,
  signalLabel,
  threatLabel
} from "./lib/format";
import { iconKindFor, isPointEvent, threatIcon } from "./lib/icons";
import { zoneFeed } from "./lib/feed";
import { activeAt, buildSlots, zoneCountsAt } from "./lib/history";
import { REGION_NEAR_WASH, regionWeight, zoneFillAlpha } from "./lib/paint";
import type { Slot } from "./lib/history";
import type { HistoryDay } from "./lib/api";
import {
  loadBookmarks,
  loadSeen,
  markSeen,
  matchBookmarks,
  toggleBookmark
} from "./lib/bookmarks";
import type { Bookmark } from "./lib/bookmarks";
import { FeedPanel } from "./panels/FeedPanel";
import { HistoryPanel } from "./panels/HistoryPanel";
import { AnalyticsPanel } from "./panels/AnalyticsPanel";
import { AlertToast } from "./panels/AlertToast";
import { BookmarksSection } from "./panels/BookmarksSection";
import { TopbarStats } from "./panels/TopbarStats";
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

type Dataset = {
  regions: GeoJsonFeatureCollection;
  districts: GeoJsonFeatureCollection;
};

type SelectedObject = {
  kind: "region" | "district" | "place";
  id: string;
  name: string;
  subtitle: string;
  details: Array<[string, string]>;
  /** Зона справочника, объявленная прямо в полигоне. */
  zone?: string | null;
};

type LayerState = {
  basemap: boolean;
  landCover: boolean;
  waterBodies: boolean;
  rivers: boolean;
  urbanAreas: boolean;
  roads: boolean;
  railways: boolean;
  regions: boolean;
  districts: boolean;
};

const WEB_MERCATOR_MAX_RESOLUTION = 156543.03392804097;
const RIVER_NETWORK_MAJOR_ZOOM = 4.8;
const RIVER_NETWORK_DETAIL_ZOOM = 7.6;
const URBAN_AREAS_ZOOM = 4.3;
// Полный набор районов — только когда пользователь действительно подошёл
// к их масштабу. Порог 4.1 был ошибкой: стартовый вид 4.15 уже выше него,
// и 14.4 МБ качались сразу, то есть лениво только на словах.
const DISTRICTS_ZOOM = 5.0;
const ROADS_ZOOM = 4.65;
const RAILWAYS_ZOOM = 4.8;
const DISTRICT_SELECTION_ZOOM = 5.4;
const BASEMAP_URL =
  import.meta.env.VITE_BASEMAP_URL || "https://{a-d}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png";
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
const STATE_POLL_MS = 25_000;


const LAYER_OPTIONS: Array<{ key: keyof LayerState; label: string; swatch: string }> = [
  { key: "basemap", label: "Подложка", swatch: "swatch-basemap" },
  { key: "regions", label: "Регионы", swatch: "swatch-region" },
  { key: "districts", label: "Районы", swatch: "swatch-district" },
  { key: "roads", label: "Дороги", swatch: "swatch-road" },
  { key: "railways", label: "Железные дороги", swatch: "swatch-railway" },
  { key: "urbanAreas", label: "Контуры городов", swatch: "swatch-urban" },
  { key: "waterBodies", label: "Водоемы", swatch: "swatch-water" },
  { key: "rivers", label: "Реки", swatch: "swatch-river" },
  { key: "landCover", label: "Леса и болота", swatch: "swatch-land-cover" }
];





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
  const zone = feature.get("zone") ? String(feature.get("zone")) : null;

  if (kind === "place") {
    const population = feature.get("population");
    return {
      kind,
      id,
      name,
      zone,
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
      zone,
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
    zone,
    subtitle: "Субъект Российской Федерации",
    details: [
      ["Тип", "Регион"],
      ["Название", name],
      ["ISO", asText(feature.get("iso"))]
    ]
  };
}

// Насыщенность заливки — от числа активных сообщений внутри зоны, как у
// Детектора АЭРО. Пустые зоны не заливаются, чтобы карта осталась читаемой.
// Ниже этого масштаба заливаются регионы, выше — районы. Красить оба сразу
// нельзя: alpha складывается и даёт грязный третий цвет.
const DISTRICT_FILL_ZOOM = 5.2;

/** Уровень опасности для фильтра: те же три ступени, что в легенде. */
function severityLevel(severity: number): number {
  if (severity >= 8) return 8;
  if (severity >= 6) return 6;
  return 4;
}

// Значки выцветают за то же время, за какое борт пересекает район: иначе на
// бледной зоне остаётся яркая метка и спорит с ней.
const ICON_FADE_MS = 30 * 60 * 1000;
const iconStyleCache = new globalThis.Map<string, Style>();

function createEventIconStyle(feature: FeatureLike, resolution: number) {
  // Порог ниже стартового масштаба (4.15): иначе значки не видны на загрузке.
  if (resolutionToZoom(resolution) < 3.9) return undefined;

  const kind = String(feature.get("iconKind"));
  const severity = asNumber(feature.get("severity"), 5);
  const ageMs = asNumber(feature.get("ageMs"), 0);

  // Свежая фиксация видна отчётливо, трёхчасовая почти растворяется.
  const freshness = Math.max(0.2, 1 - Math.min(1, ageMs / ICON_FADE_MS) ** 0.5);
  const bucket = Math.round(freshness * 5) / 5;
  const key = `${kind}|${severity}|${bucket}`;

  const cached = iconStyleCache.get(key);
  if (cached) return cached;

  const style = new Style({
    image: new Icon({
      src: threatIcon(kind as never, severityColor(severity, 1), bucket),
      scale: 0.62,
      anchor: [0.5, 0.5]
    })
  });
  iconStyleCache.set(key, style);
  return style;
}

function createRegionStyle(
  selectedKeyRef: React.MutableRefObject<string | null>,
  zoneStateRef: React.MutableRefObject<globalThis.Map<string, ZoneCount>>
) {
  return (feature: FeatureLike, resolution: number) => {
    const selected = selectedKeyRef.current === featureKey(feature);
    const active = zoneStateRef.current.get(String(feature.get("id")));
    // Ниже порога регион закрашивается по любой тревоге внутри — это обзор.
    // Выше порога закрашивают районы, но регион со своим собственным
    // оповещением («опасность по области») закрашивается и здесь: иначе от
    // него на карте оставалась одна метка и ни одной выделенной зоны.
    const overview = resolutionToZoom(resolution) < DISTRICT_FILL_ZOOM;
    const ownHere = (active?.own ?? 0) > 0;
    const zone = overview || ownHere ? active : undefined;
    const fillColor = zone
      ? overview
        // Вес по охвату: регион, подсвеченный одной фиксацией в одном
        // районе, почти прозрачен, а объявленная по всей области опасность
        // закрашена в полную силу. Уровень и свежесть приходят от одного и
        // того же события — самого весомого сейчас.
        ? severityColor(
            zone.severity,
            zoneFillAlpha(zone.active) * regionWeight(zone.own, zone.active) * zone.fade
          )
        // Вблизи регион красится только своим уровнем и вполсилы: здесь
        // сигнал — район, а область только фон. В полную силу этот фон
        // забивал всё под собой, и погасший район выглядел таким же
        // красным, как горящий рядом.
        : severityColor(
            zone.own_severity,
            zoneFillAlpha(zone.own) * zone.own_fade * REGION_NEAR_WASH
          )
      : selected
        ? "rgba(228, 178, 93, 0.055)"
        : "rgba(255, 255, 255, 0.006)";

    return [
      new Style({ fill: new Fill({ color: fillColor }) }),
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

function createDistrictStyle(
  selectedKeyRef: React.MutableRefObject<string | null>,
  zoneStateRef: React.MutableRefObject<globalThis.Map<string, ZoneCount>>
) {
  return (feature: FeatureLike, resolution: number) => {
    const paintHere = resolutionToZoom(resolution) >= DISTRICT_FILL_ZOOM;
    const selected = selectedKeyRef.current === featureKey(feature);
    const zone = paintHere ? zoneStateRef.current.get(String(feature.get("id"))) : undefined;

    if (zone) {
      // Свежая тревога горит ярко, часовой давности — вполовину тусклее.
      // Уровень берётся от того же события, что и свежесть: иначе двухчасовая
      // фиксация красила бы район в полный красный, стоило прийти любому
      // новому сообщению по соседству.
      const painted = [
        new Style({
          fill: new Fill({
            color: severityColor(zone.severity, zoneFillAlpha(zone.active) * zone.fade)
          }),
          stroke: new Stroke({
            color: severityColor(zone.severity, 0.62 * zone.fade),
            width: 1
          })
        })
      ];
      // Выбранный район с событиями до этой проверки не доходил вовсе:
      // ветка с заливкой возвращалась раньше. Человек нажимал на район,
      // не видел его границы и решал, что ему показали соседей.
      if (selected) painted.push(SELECTION_OUTLINE);
      return painted;
    }

    if (selected) {
      return [
        new Style({ fill: new Fill({ color: "rgba(246, 199, 61, 0.09)" }) }),
        SELECTION_OUTLINE
      ];
    }

    // Пустые районы не рисуются, пока карта работает на уровне регионов:
    // 2327 контуров без единого события — это шум, забивающий заливку,
    // которая и есть сигнал. Сетка появляется там же, где карта переходит
    // на районный уровень, и дальше проявляется с приближением.
    const zoom = resolutionToZoom(resolution);
    if (zoom < DISTRICT_FILL_ZOOM) return EMPTY_STYLE;

    const growth = clamp01((zoom - DISTRICT_FILL_ZOOM) / 1.8);

    return new Style({
      fill: new Fill({ color: "rgba(255, 255, 255, 0.004)" }),
      stroke: new Stroke({
        color: `rgba(196, 208, 202, ${0.16 + growth * 0.28})`,
        width: 0.45 + growth * 0.45
      })
    });
  };
}

// Обводка выбранной зоны. Рисуется поверх заливки, поэтому вынесена
// отдельно: иначе выбранный район с событиями оставался без границы.
const SELECTION_OUTLINE = new Style({
  stroke: new Stroke({ color: "rgba(255, 248, 220, 0.95)", width: 2.6 })
});

// Стиль без отрисовки: возвращать undefined нельзя — слой всё равно должен
// оставаться кликабельным для выбора района.
const EMPTY_STYLE = new Style({
  fill: new Fill({ color: "rgba(255, 255, 255, 0.002)" })
});

function clamp01(value: number): number {
  return Math.max(0, Math.min(1, value));
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







async function loadDataset(signal?: AbortSignal): Promise<Dataset> {
  const read = async (url: string) => {
    const response = await fetch(url, { signal });
    if (!response.ok) throw new Error(`${url}: ${response.status} ${response.statusText}`);
    return response.json();
  };

  // Районы сюда не входят намеренно: 14.4 МБ на 2327 полигонов, из которых
  // на стартовом масштабе не рисуется ни один. Они подгружаются по зуму.
  const regions = await read(`${API_BASE}/api/v1/geo/regions.geojson`).catch(() =>
    read("/data/regions.json")
  );

  return { regions, districts: { type: "FeatureCollection", features: [] } };
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
  const districtsLoadedRef = useRef(false);
  const regionLayerRef = useRef<VectorLayer<VectorSource<Feature<Geometry>>> | null>(null);
  const districtLayerRef = useRef<VectorLayer<VectorSource<Feature<Geometry>>> | null>(null);
  // Состояние зон, ключ — source_id полигона в regions.json / districts.json.
  const zoneStateRef = useRef<globalThis.Map<string, ZoneCount>>(new globalThis.Map());
  const eventIconSourceRef = useRef<VectorSource<Feature<Geometry>>>(new VectorSource());
  const eventIconLayerRef = useRef<VectorLayer<VectorSource<Feature<Geometry>>> | null>(null);
  const polygonToZoneRef = useRef<globalThis.Map<string, string>>(new globalThis.Map());
  const selectedKeyRef = useRef<string | null>(null);
  const layersRef = useRef<LayerState | null>(null);
  const loadLazyLayersRef = useRef<(() => void) | null>(null);
  const featureIndexRef = useRef<globalThis.Map<string, Feature<Geometry>>>(new globalThis.Map());

  const [dataset, setDataset] = useState<Dataset | null>(null);
  const [selected, setSelected] = useState<SelectedObject>(emptySelected);
  // source_id полигона региона, внутри которого лежит выбранный район.
  const [selectedRegionPolygon, setSelectedRegionPolygon] = useState<string | null>(null);
  // Значок под курсором и его место на экране — для всплывающей подсказки.
  const [iconHint, setIconHint] = useState<{ feature: FeatureLike; pixel: number[] } | null>(null);
  const [radarState, setRadarState] = useState<RadarState | null>(null);
  const [apiOnline, setApiOnline] = useState<boolean | null>(null);
  const [layers, setLayers] = useState<LayerState>({
    basemap: true,
    landCover: false,
    waterBodies: false,
    rivers: false,
    urbanAreas: false,
    roads: false,
    railways: false,
    regions: true,
    districts: true,
  });
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const deferredQuery = useDeferredValue(query);

  const [suggestions, setSuggestions] = useState<SearchItem[]>([]);
  const [highlighted, setHighlighted] = useState(0);
  const [bookmarks, setBookmarks] = useState<Bookmark[]>(() => loadBookmarks());
  const [alerts, setAlerts] = useState<RadarEvent[]>([]);
  const [analyticsOpen, setAnalyticsOpen] = useState(false);
  // На узком экране панели занимают почти весь экран, поэтому там они
  // стартуют свёрнутыми: приоритет у карты, панель открывается по нажатию.
  const [leftOpen, setLeftOpen] = useState(
    () => !window.matchMedia(MOBILE_QUERY).matches
  );
  // Границы видимой области карты в проекции карты. Обновляются по окончании
  // движения: пересчитывать на каждый кадр незачем.
  const [viewExtent, setViewExtent] = useState<number[] | null>(null);
  const [onlyVisible, setOnlyVisible] = useState(true);
  const [levelFilter, setLevelFilter] = useState<number[]>([]);
  const [threatFilter, setThreatFilter] = useState<string[]>([]);
  // Лента открыта всегда: ради неё сюда и приходят. Свернуть её можно
  // стрелкой, а вот открывать закрытую панель, чтобы узнать обстановку, —
  // лишний шаг на каждом заходе.
  const [rightOpen, setRightOpen] = useState(true);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyEvents, setHistoryEvents] = useState<RadarEvent[] | null>(null);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [slots, setSlots] = useState<Slot[]>([]);
  const [slotIndex, setSlotIndex] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [historyDays, setHistoryDays] = useState<HistoryDay[]>([]);
  const [selectedDay, setSelectedDay] = useState<string | null>(null);
  const [historySpeed, setHistorySpeed] = useState(1);

  // Поиск выполняет сервер по индексу справочника: 212 тысяч зон, ответ за
  // десятки миллисекунд. Держать этот каталог в браузере было незачем.
  useEffect(() => {
    const normalized = deferredQuery.trim();
    if (normalized.length < 2) {
      setSuggestions([]);
      return;
    }

    const controller = new AbortController();
    const timer = window.setTimeout(async () => {
      try {
        const response = await fetch(
          `${API_BASE}/api/v1/search?q=${encodeURIComponent(normalized)}&limit=${MAX_SUGGESTIONS}`,
          { signal: controller.signal }
        );
        if (!response.ok) return;
        const payload = (await response.json()) as { items: SearchItem[] };
        setSuggestions(payload.items);
        setHighlighted(0);
      } catch {
        // Прерванный или неудачный запрос просто не меняет подсказки.
      }
    }, 180);

    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [deferredQuery]);

  // История подгружается один раз при открытии плеера: дальше перемотка идёт
  // по уже полученным событиям и не трогает сервер.
  useEffect(() => {
    if (!historyOpen || historyDays.length) return;
    const controller = new AbortController();
    api
      .historyDays(60, controller.signal)
      .then((payload) => {
        if (!controller.signal.aborted) setHistoryDays(payload.days);
      })
      .catch(() => undefined);
    return () => controller.abort();
  }, [historyOpen, historyDays.length]);

  // Выбранные сутки загружаются отдельно от суточного окна: человек мыслит
  // своим днём, а не «последними 24 часами».
  useEffect(() => {
    if (!historyOpen || !selectedDay) return;
    const controller = new AbortController();
    setHistoryLoading(true);

    api
      .historyDay(selectedDay, controller.signal)
      .then((payload) => {
        if (controller.signal.aborted) return;
        const built = buildSlots(payload.from, payload.to);
        setHistoryEvents(payload.events);
        setSlots(built);
        setSlotIndex(0);
        setHistoryLoading(false);
      })
      .catch(() => {
        if (!controller.signal.aborted) setHistoryLoading(false);
      });

    return () => controller.abort();
  }, [historyOpen, selectedDay]);

  // Диаграмма активности: сколько событий было в каждом срезе и какого
  // уровня. Без неё перемотка вслепую — полоса тянется на сутки, а всплеск
  // занимает в ней двадцать минут, и найти его можно было только наугад.
  const slotLoad = useMemo(() => {
    if (!historyEvents || slots.length === 0) return [];
    return slots.map((slot) => {
      const at = new Date(slot.at).getTime();
      let count = 0;
      let severity = 0;
      for (const event of historyEvents) {
        if (!activeAt(event, at)) continue;
        count += 1;
        severity = Math.max(severity, event.severity);
      }
      return { count, severity };
    });
  }, [historyEvents, slots]);

  useEffect(() => {
    if (!historyOpen || historyEvents || selectedDay) return;
    const controller = new AbortController();
    setHistoryLoading(true);

    api
      .history(24, controller.signal)
      .then((payload) => {
        if (controller.signal.aborted) return;
        const built = buildSlots(payload.from, payload.to);
        setHistoryEvents(payload.events);
        setSlots(built);
        setSlotIndex(Math.max(0, built.length - 1));
        setHistoryLoading(false);
      })
      .catch(() => {
        if (!controller.signal.aborted) setHistoryLoading(false);
      });

    return () => controller.abort();
  }, [historyOpen, historyEvents]);

  /** Полигон региона, внутри которого лежит выбранный район. */
  const regionPolygonOf = useCallback((feature: FeatureLike): string | null => {
    if (String(feature.get("kind") ?? "") === "region") return String(feature.get("id") ?? "");
    // Родителя записал справочник — он его знает точно. Поиск геометрией
    // оставлен запасным: у изогнутого района любая пробная точка норовит
    // попасть в соседний субъект, и лента показывала обстановку соседей.
    const declared = feature.get("region");
    if (declared) return String(declared);

    const geometry = (feature as Feature<Geometry>).getGeometry?.();
    if (!geometry) return null;
    const probe = geometry.getClosestPoint(getCenter(geometry.getExtent()));
    const source = regionLayerRef.current?.getSource();
    if (!source) return null;
    let found: string | null = null;
    source.forEachFeatureAtCoordinateDirect(probe, (candidate) => {
      found = String(candidate.get("id") ?? "");
      return true;
    });
    return found;
  }, []);

  const applySelectedFeature = useCallback((feature: FeatureLike | null) => {
    selectedKeyRef.current = feature ? featureKey(feature) : null;
    setSelected(feature ? selectedFromFeature(feature) : emptySelected);
    // Регион выбранного района запоминается сразу: если в самом районе
    // сообщений нет, лента показывает обстановку по области — иначе
    // непонятно, почему район вообще закрашен.
    setSelectedRegionPolygon(feature ? regionPolygonOf(feature) : null);
    regionLayerRef.current?.changed();
    districtLayerRef.current?.changed();
    // Выбор места — это вопрос «что там происходит», и ответ лежит в ленте.
    // Со свёрнутой панелью нажатие на район не показывало ничего.
    if (feature) {
      setRightOpen(true);
      if (window.matchMedia(MOBILE_QUERY).matches) setLeftOpen(false);
    }
  }, []);

  useEffect(() => {
    // AbortController обязателен: в StrictMode эффект вызывается дважды, и без
    // отмены districts.json скачивается два раза по 14 МБ.
    const controller = new AbortController();
    setLoading(true);

    loadDataset(controller.signal)
      .then((data) => {
        if (controller.signal.aborted) return;
        setDataset(data);
        setLoading(false);
      })
      .catch((reason: unknown) => {
        if (controller.signal.aborted) return;
        setError(reason instanceof Error ? reason.message : "Не удалось загрузить данные карты");
        setLoading(false);
      });

    return () => controller.abort();
  }, []);

  // Обстановка из API конвейера. Карта работает и без него — просто без событий.
  useEffect(() => {
    let active = true;

    const pull = async () => {
      try {
        const payload = await api.state();
        if (!active) return;
        setRadarState(payload);
        setApiOnline(true);
      } catch {
        if (active) setApiOnline(false);
      }
    };

    void pull();

    // Push с сервера. Опрос остаётся подстраховкой: если сокет не поднялся
    // или оборвался, карта продолжает обновляться, просто реже.
    let socket: WebSocket | null = null;
    try {
      socket = new WebSocket(`${API_BASE.replace(/^http/, "ws")}/api/v1/stream`);
      socket.onmessage = (event) => {
        if (!active) return;
        try {
          const payload = JSON.parse(event.data) as RadarState & { type?: string };
          if (payload.type === "state") {
            setRadarState(payload);
            setApiOnline(true);
          }
        } catch {
          // Битый кадр пропускаем: следующий придёт через несколько секунд.
        }
      };
      socket.onerror = () => socket?.close();
    } catch {
      socket = null;
    }

    const timer = window.setInterval(pull, STATE_POLL_MS);
    return () => {
      active = false;
      window.clearInterval(timer);
      socket?.close();
    };
  }, []);

  const inHistory =
    historyOpen && historyEvents !== null && (selectedDay !== null || slotIndex < slots.length - 1);
  const historyAt = inHistory ? slots[slotIndex]?.at ?? null : null;

  // Карта красится одной и той же формой счётчиков — живой или исторической.
  const paintedZones = useMemo(() => {
    if (inHistory && historyEvents && historyAt) {
      return zoneCountsAt(historyEvents, historyAt, radarState?.zone_counts ?? {});
    }
    return radarState?.zone_counts ?? {};
  }, [inHistory, historyEvents, historyAt, radarState]);

  const shownEvents = useMemo(() => {
    if (inHistory && historyEvents && historyAt) {
      const atMs = new Date(historyAt).getTime();
      // Без среза: карта обязана показать всё, что было в этот момент.
      // Шестьдесят — это предел списка в ленте, и он режется там же.
      return historyEvents
        .filter((event) => new Date(event.first_seen_at).getTime() <= atMs)
        .filter((event) => !event.resolved_at || new Date(event.resolved_at).getTime() > atMs);
    }
    return radarState?.events ?? [];
  }, [inHistory, historyEvents, historyAt, radarState]);

  // Обстановка рисуется заливкой самих регионов и районов, а не отдельными
  // маркерами: так делают RadarMap и Детектор АЭРО, и так понятнее.
  useEffect(() => {
    const index = new globalThis.Map<string, ZoneCount>();
    for (const zone of Object.values(paintedZones)) {
      if (!zone.source_id) continue;
      if (zone.level !== "region" && zone.level !== "district") continue;
      index.set(zone.source_id, zone);
    }
    zoneStateRef.current = index;

    // Значки ставятся только там, где у сообщения есть конкретная точка:
    // фиксация, сбитие, взрыв, отбой. Площадная опасность — это заливка.
    const iconSource = eventIconSourceRef.current;
    iconSource.clear();
    const referenceMs = new Date(historyAt ?? radarState?.generated_at ?? Date.now()).getTime();

    for (const event of shownEvents) {
      // Закрытое событие в ленте остаётся отбоем, но значок на карте
      // означает «здесь сейчас», и ему там не место.
      //
      // В истории это правило не работает: status рассказывает про сейчас,
      // а не про просматриваемый момент, и все прошлые события в нём
      // «resolved». Из-за этого перемотка показывала заливку без единого
      // значка. Отбор по времени уже сделан выше — сюда доходят только те
      // события, что на тот момент были открыты.
      if (!inHistory && event.status === "resolved") continue;
      if (!isPointEvent(event.signal_type, event.zone_level)) continue;
      if (typeof event.lat !== "number" || typeof event.lon !== "number") continue;

      iconSource.addFeature(
        new Feature({
          geometry: new Point(fromLonLat([event.lon, event.lat])),
          kind: "eventIcon",
          id: event.id,
          iconKind: iconKindFor(event.signal_type, event.threat_type),
          severity: event.severity,
          // Подпись подсказки: значок стоит в точке события, а точка
          // события — центр его зоны, и в тихом районе рядом он выглядит
          // необъяснимо. Подсказка отвечает, чей он и когда поставлен.
          title: event.place_name,
          signal: signalLabel(event.signal_type),
          threat: event.threat_type === "unknown" ? "" : threatLabel(event.threat_type),
          sources: event.source_count,
          at: event.last_seen_at,
          ageMs: Math.max(0, referenceMs - new Date(event.last_seen_at).getTime())
        })
      );
    }

    const byPolygon = new globalThis.Map<string, string>();
    for (const [zoneId, zone] of Object.entries(radarState?.zone_counts ?? {})) {
      if (zone.source_id) byPolygon.set(zone.source_id, zoneId);
    }
    polygonToZoneRef.current = byPolygon;
    regionLayerRef.current?.changed();
    districtLayerRef.current?.changed();
  }, [paintedZones, radarState, shownEvents, historyAt]);

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
    const eventIconLayer = new VectorLayer({
      source: eventIconSourceRef.current,
      zIndex: 40,
      style: createEventIconStyle
    });

    const regionLayer = new VectorLayer({
      source: regionSource,
      visible: layers.regions,
      zIndex: 28,
      style: createRegionStyle(selectedKeyRef, zoneStateRef)
    });
    const districtLayer = new VectorLayer({
      source: districtSource,
      visible: layers.districts,
      zIndex: 20,
      minZoom: 4.15,
      style: createDistrictStyle(selectedKeyRef, zoneStateRef)
    });

    basemapLayerRef.current = basemapLayer;
    hillshadeLayerRef.current = hillshadeLayer;
    landCoverLayerRef.current = landCoverLayer;
    waterBodyLayerRef.current = waterBodyLayer;
    riverLayerRef.current = riverLayer;
    riverNetworkMajorLayerRef.current = riverNetworkMajorLayer;
    riverNetworkDetailLayerRef.current = riverNetworkDetailLayer;
    urbanAreaLayerRef.current = urbanAreaLayer;
    roadLayerRef.current = roadLayer;
    railwayLayerRef.current = railwayLayer;
    eventIconLayerRef.current = eventIconLayer;
    regionLayerRef.current = regionLayer;
    districtLayerRef.current = districtLayer;

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
        eventIconLayer
      ],
      view: new View({
        center: OVERVIEW_CENTER,
        zoom: getOverviewZoom(),
        minZoom: 1.75,
        maxZoom: 9.8
      })
    });

    // Контейнер позиционирован абсолютно, и на момент создания его размер
    // может быть ещё нулевым — OpenLayers запомнит ноль и больше ничего не
    // нарисует. Стартовый вид тоже нельзя задавать раньше: выбор десктопного
    // или мобильного масштаба зависит от фактической ширины.
    let viewApplied = false;
    const resizeObserver = new ResizeObserver(() => {
      map.updateSize();
      const [width, height] = map.getSize() ?? [0, 0];
      if (width > 0 && height > 0) {
        if (!viewApplied) {
          viewApplied = true;
          setOverviewView(map, 0);
        }
        setViewExtent(map.getView().calculateExtent([width, height]));
      }
    });
    resizeObserver.observe(mapNodeRef.current);
    map.updateSize();

    if ((map.getSize()?.[0] ?? 0) > 0) {
      viewApplied = true;
      setOverviewView(map, 0);
    }
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
        if (source === districtSource) {
          // Частичный набор активных зон уступает место полному.
          source.clear();
          features.forEach((feature) => {
            feature.set("kind", "district");
            featureIndexRef.current.set(featureKey(feature), feature);
          });
          window.clearInterval(activeDistrictsTimer);
        }
        source.addFeatures(features);
        if (source === districtSource) districtLayerRef.current?.changed();
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
      if (currentLayers.districts && zoom >= DISTRICTS_ZOOM) {
        void loadVectorLayer("/data/districts.json", districtSource, districtsLoadedRef, "районов");
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

    // Активные районы приходят отдельным лёгким запросом, чтобы подсветка
    // была видна сразу, не дожидаясь полного набора полигонов.
    const loadActiveDistricts = async () => {
      if (districtsLoadedRef.current) return;
      try {
        const response = await fetch(`${API_BASE}/api/v1/geo/active?levels=district`);
        if (!response.ok) return;
        const data = (await response.json()) as GeoJsonFeatureCollection;
        if (disposed || districtsLoadedRef.current) return;

        const known = new Set(districtSource.getFeatures().map((item) => String(item.get("id"))));
        const fresh = (geoJson.readFeatures(data, {
          dataProjection: "EPSG:4326",
          featureProjection: "EPSG:3857"
        }) as Feature<Geometry>[]).filter((item) => !known.has(String(item.get("id"))));

        fresh.forEach((feature) => {
          feature.set("kind", "district");
          featureIndexRef.current.set(featureKey(feature), feature);
        });
        districtSource.addFeatures(fresh);
        districtLayerRef.current?.changed();
      } catch {
        // Подсветка появится, когда подгрузится полный набор районов.
      }
    };

    void loadActiveDistricts();
    const activeDistrictsTimer = window.setInterval(loadActiveDistricts, 60_000);

    loadLazyLayersRef.current = maybeLoadLazyLayers;
    const syncExtent = () => {
      const size = map.getSize();
      if (size && size[0] > 0 && size[1] > 0) {
        setViewExtent(map.getView().calculateExtent(size));
      }
    };
    map.on("moveend", syncExtent);
    syncExtent();

    const viewResolutionKey = map.getView().on("change:resolution", () => {
      maybeLoadLazyLayers();
      regionLayerRef.current?.changed();
      districtLayerRef.current?.changed();
    });
    maybeLoadLazyLayers();

    map.on("pointermove", (event) => {
      const hit = map.hasFeatureAtPixel(event.pixel, {
        hitTolerance: 5,
        layerFilter: (layer) => layer === regionLayer || layer === districtLayer
      });
      const target = map.getTargetElement();
      if (target) target.style.cursor = hit ? "pointer" : "";

      // Значок стоит в центре своей зоны, а не там, где борт: в тихом
      // районе по соседству он выглядел необъяснимо. Подсказка отвечает,
      // чей это значок и когда он поставлен.
      let icon: FeatureLike | null = null;
      map.forEachFeatureAtPixel(
        event.pixel,
        (feature) => { icon = feature; return true; },
        { hitTolerance: 6, layerFilter: (layer) => layer === eventIconLayer }
      );
      setIconHint(icon ? { feature: icon, pixel: event.pixel } : null);
      if (icon && target) target.style.cursor = "pointer";
    });

    map.on("singleclick", (event) => {
      const zoom = map.getView().getZoom() ?? 0;
      let regionFeature: FeatureLike | null = null;
      let districtFeature: FeatureLike | null = null;

      map.forEachFeatureAtPixel(
        event.pixel,
        (feature, layer) => {
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
          layerFilter: (layer) => layer === districtLayer || layer === regionLayer
        }
      );

      const picked =
        zoom >= DISTRICT_SELECTION_ZOOM
          ? districtFeature ?? regionFeature
          : regionFeature ?? districtFeature;

      // Повторное нажатие по уже выбранному месту снимает выбор: иначе
      // выйти из него можно было только крестиком в ленте, а рука тянется
      // ткнуть туда же ещё раз.
      const same = picked && featureKey(picked) === selectedKeyRef.current;
      applySelectedFeature(same ? null : picked);
    });

    mapRef.current = map;

    return () => {
      disposed = true;
      window.clearInterval(activeDistrictsTimer);
      resizeObserver.disconnect();
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
      districtsLoadedRef.current = false;
      loadLazyLayersRef.current = null;
      layersRef.current = null;
      eventIconLayerRef.current = null;
      regionLayerRef.current = null;
      districtLayerRef.current = null;
      featureIndexRef.current.clear();
    };
  }, [applySelectedFeature, dataset]);

  useEffect(() => {
    layersRef.current = layers;
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
    loadLazyLayersRef.current?.();
  }, [layers]);

  // Лента показывает то, что человек видит на экране: карта и список
  // перестают жить отдельными жизнями. Отключается тумблером «в кадре».
  const feedEvents = useMemo(() => {
    let list = shownEvents;

    const usableExtent =
      viewExtent && viewExtent[2] > viewExtent[0] && viewExtent[3] > viewExtent[1]
        ? viewExtent
        : null;

    if (onlyVisible && usableExtent) {
      list = list.filter((event) => {
        if (typeof event.lat !== "number" || typeof event.lon !== "number") return false;
        return containsCoordinate(usableExtent, fromLonLat([event.lon, event.lat]));
      });
    }
    if (levelFilter.length) {
      list = list.filter((event) => levelFilter.includes(severityLevel(event.severity)));
    }
    if (threatFilter.length) {
      list = list.filter((event) => threatFilter.includes(event.threat_type));
    }
    return list;
  }, [shownEvents, onlyVisible, viewExtent, levelFilter, threatFilter]);

  // Что показать в ленте при выбранном месте. Правило вынесено в lib/feed.ts
  // и покрыто тестами: здесь оно трижды разошлось с ожиданием.
  const selectedFeed = useMemo(() => {
    if (selected.id === "none" || selected.kind === "place") {
      return { events: [] as RadarEvent[], fromRegion: false };
    }
    const zoneId = polygonToZoneRef.current.get(selected.id) ?? null;
    const regionZone = selectedRegionPolygon
      ? polygonToZoneRef.current.get(selectedRegionPolygon) ?? null
      : null;
    return zoneFeed(feedEvents, zoneId, regionZone);
  }, [feedEvents, selected, selectedRegionPolygon]);

  const selectedZoneEvents = selectedFeed.events;
  const zoneEventsFromRegion = selectedFeed.fromRegion;

  const selectedRegionName = useMemo(() => {
    if (!selectedRegionPolygon) return null;
    const zoneId = polygonToZoneRef.current.get(selectedRegionPolygon);
    return zoneId ? paintedZones[zoneId]?.name ?? null : null;
  }, [selectedRegionPolygon, paintedZones]);

  // Зона выбранного места. Соответствие полигонов зонам строится из
  // счётчиков обстановки и потому знает только шумные места; в самом
  // полигоне зона объявлена всегда, поэтому подписаться можно и на тихий
  // город — то есть заранее, когда это и нужно.
  const selectedZoneId = useMemo(() => {
    if (selected.id === "none") return null;
    return selected.zone ?? polygonToZoneRef.current.get(selected.id) ?? null;
  }, [selected]);

  const selectSearchItem = useCallback(
    (item: SearchItem) => {
      const map = mapRef.current;
      if (!map) return;

      // У региона и района полигон уже загружен — подлетаем к его границам.
      if (item.source_id && item.level !== "place") {
        const feature = featureIndexRef.current.get(`${item.level}:${item.source_id}`);
        if (feature) {
          applySelectedFeature(feature);
          fitFeature(map, feature, item.level === "region" ? 5.2 : 7.4);
          setQuery(item.name);
          setSuggestions([]);
          return;
        }
      }

      // Населённый пункт живёт только на сервере — летим по координатам.
      if (typeof item.lat === "number" && typeof item.lon === "number") {
        applySelectedFeature(null);
        map.getView().animate({
          center: fromLonLat([item.lon, item.lat]),
          zoom: Math.max(map.getView().getZoom() ?? 5, 8.8),
          duration: 420
        });
      }
      setQuery(item.name);
      setSuggestions([]);
    },
    [applySelectedFeature]
  );

  const flyToEvent = useCallback((event: RadarEvent) => {
    const map = mapRef.current;
    if (!map || typeof event.lat !== "number" || typeof event.lon !== "number") return;
    const zoom = event.zone_level === "region" ? 5.6 : event.zone_level === "district" ? 7.2 : 8.4;
    map.getView().animate({
      center: fromLonLat([event.lon, event.lat]),
      zoom: Math.max(map.getView().getZoom() ?? 4, zoom),
      duration: 420
    });
  }, []);

  const openLeft = useCallback(() => {
    setLeftOpen(true);
    if (window.matchMedia(MOBILE_QUERY).matches) setRightOpen(false);
  }, []);

  const openRight = useCallback(() => {
    setRightOpen(true);
    if (window.matchMedia(MOBILE_QUERY).matches) setLeftOpen(false);
  }, []);

  const flyToBookmark = useCallback(
    (bookmark: Bookmark) => {
      const map = mapRef.current;
      if (!map) return;

      // У региона и района геометрия уже загружена — подлетаем к границам.
      if (bookmark.source_id) {
        const level = bookmark.level === "region" ? "region" : "district";
        const feature = featureIndexRef.current.get(`${level}:${bookmark.source_id}`);
        if (feature) {
          applySelectedFeature(feature);
          fitFeature(map, feature, bookmark.level === "region" ? 5.2 : 7.4);
          return;
        }
      }

      if (typeof bookmark.lat === "number" && typeof bookmark.lon === "number") {
        map.getView().animate({
          center: fromLonLat([bookmark.lon, bookmark.lat]),
          zoom: Math.max(map.getView().getZoom() ?? 5, 7.4),
          duration: 420
        });
      }
    },
    [applySelectedFeature]
  );

  const removeBookmark = useCallback((zoneId: string) => {
    setBookmarks((current) => {
      const target = current.find((item) => item.zone_id === zoneId);
      return target ? toggleBookmark(current, target) : current;
    });
  }, []);

  const handleToggleBookmark = useCallback(() => {
    if (selected.id === "none" || selected.kind === "place") return;
    const zoneId = polygonToZoneRef.current.get(selected.id);
    if (!zoneId) return;
    const zone = radarState?.zone_counts?.[zoneId];
    setBookmarks((current) =>
      toggleBookmark(current, {
        zone_id: zoneId,
        name: selected.name,
        level: selected.kind,
        context: null,
        lat: null,
        lon: null,
        source_id: zone?.source_id ?? selected.id
      })
    );
  }, [selected, radarState]);

  // Уведомление по отслеживаемым местам — один раз на событие.
  useEffect(() => {
    if (!radarState || !bookmarks.length || historyAt) return;
    const seen = new Set(loadSeen());
    const fresh = matchBookmarks(radarState.events, bookmarks).filter(
      (event) => !seen.has(event.id)
    );
    if (!fresh.length) return;
    setAlerts(fresh);
    markSeen(fresh.map((event) => event.id));
  }, [radarState, bookmarks, historyAt]);

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

        <TopbarStats
          events={radarState ? shownEvents : null}
          zones={Object.keys(paintedZones).length}
          historyLabel={historyAt ? formatDayTime(historyAt) : null}
          moment={radarState?.generated_at ?? null}
        />

      </header>

      <main className="workspace">
        <button
          className={`panel-handle handle-left ${leftOpen ? "is-hidden" : ""}`}
          type="button"
          onClick={openLeft}
          aria-label="Поиск места и что значат цвета"
        >
          <SlidersHorizontal size={17} aria-hidden="true" />
          {/* Подпись обязательна: это единственный вход в поиск и легенду, а
              безымянную иконку человек с улицы просто не находит — цвета на
              карте так и остаются нерасшифрованными. */}
          <span className="handle-label">
            Поиск<span className="handle-label-more"> и легенда</span>
          </span>
        </button>

        <aside
          className={`sidebar ${leftOpen ? "" : "is-collapsed"}`}
          aria-label="Панель управления картой"
        >

          <section className="tool-section">
            <div className="search-row">
            <label className="search-box" htmlFor="map-search">
              <Search size={18} aria-hidden="true" />
              <input
                id="map-search"
                type="search"
                value={query}
                placeholder="Найти город или район"
                autoComplete="off"
                role="combobox"
                aria-expanded={suggestions.length > 0}
                aria-controls="search-suggestions"
                onChange={(event) => setQuery(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Escape") {
                    setSuggestions([]);
                    return;
                  }
                  if (!suggestions.length) return;
                  if (event.key === "ArrowDown") {
                    event.preventDefault();
                    setHighlighted((index) => (index + 1) % suggestions.length);
                  } else if (event.key === "ArrowUp") {
                    event.preventDefault();
                    setHighlighted((index) => (index - 1 + suggestions.length) % suggestions.length);
                  } else if (event.key === "Enter") {
                    event.preventDefault();
                    selectSearchItem(suggestions[highlighted] ?? suggestions[0]);
                  }
                }}
              />
            </label>
            <button
              className="panel-collapse"
              type="button"
              onClick={() => setLeftOpen(false)}
              aria-label="Свернуть панель"
            >
              <ChevronLeft size={16} aria-hidden="true" />
            </button>
            </div>
            {suggestions.length > 0 ? (
              <div className="suggestions" id="search-suggestions" role="listbox" aria-label="Найденные объекты">
                {suggestions.map((item, index) => (
                  <button
                    key={item.zone_id}
                    type="button"
                    role="option"
                    aria-selected={index === highlighted}
                    className={index === highlighted ? "is-active" : undefined}
                    onMouseEnter={() => setHighlighted(index)}
                    onClick={() => selectSearchItem(item)}
                  >
                    <span>{item.name}</span>
                    <small>{item.context ?? "Регион"}</small>
                  </button>
                ))}
              </div>
            ) : null}
          </section>

          <section className="tool-section">
            {/* Легенда обязана описывать то, что на карте, а не то, что
                задумывалось, — и говорить это в одну строку. Подробности
                про скорость борта и размер зоны живут в подсказках: они
                нужны тому, кто спросит, и мешают тому, кто не спрашивал. */}
            <div className="severity-legend">
              <span data-tip="Борт уже видят: фиксация, работа ПВО, взрыв. Это про то, ЧТО сообщили, а не про то, сколько лент это подтвердило">
                <i style={{ background: severityColor(9, 0.95) }} aria-hidden="true" />Фиксация, взрыв, ПВО
              </span>
              <span data-tip="Объявлена тревога или звучит сирена, но подтверждённой фиксации нет">
                <i style={{ background: severityColor(7, 0.95) }} aria-hidden="true" />Тревога
              </span>
              <span data-tip="Предупреждение об опасности: борт может прилететь">
                <i style={{ background: severityColor(5, 0.95) }} aria-hidden="true" />Опасность
              </span>
            </div>

            <p className="legend-note" data-tip="Зона гаснет за то время, за какое борт её пересекает: район — за полчаса, область — часа за три. Область закрашивается бледнее районов: тревога объявлена по ней целиком, а не по каждому её району">
              Ярче — свежее. Погасло — прошло.
            </p>

            <button className="ghost-button" type="button" onClick={resetMap}>
              <Home size={17} aria-hidden="true" />
              <span>Сбросить вид</span>
            </button>
          </section>

          <BookmarksSection
            bookmarks={bookmarks}
            state={radarState}
            onPick={flyToBookmark}
            onRemove={removeBookmark}
          />

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

          <details className="disclaimer">
            <summary>Неофициальная карта · о данных</summary>
            <p>
              Составлена по публичным сообщениям, может опаздывать и ошибаться.
              Не принимайте по ней решения о личной безопасности — следуйте
              указаниям экстренных служб.
            </p>
          </details>
        </aside>

        <section className="map-panel" aria-label="Интерактивная карта">
          <div className="map-surface" ref={mapNodeRef} />

          {/* Подсказка к значку. Значок стоит в центре своей зоны, а не там,
              где борт: без подписи он выглядит меткой соседнего района, в
              котором «сообщений нет». */}
          {iconHint ? (
            <div
              className="icon-hint"
              style={{ left: iconHint.pixel[0], top: iconHint.pixel[1] }}
              role="tooltip"
            >
              <b>{asText(iconHint.feature.get("title"), "—")}</b>
              <span>
                {asText(iconHint.feature.get("signal"))}
                {iconHint.feature.get("threat") ? ` · ${asText(iconHint.feature.get("threat"))}` : ""}
              </span>
              <span className="icon-hint-foot">
                {formatMoment(
                  asText(iconHint.feature.get("at")),
                  historyAt ?? radarState?.generated_at ?? new Date().toISOString()
                )}
                {Number(iconHint.feature.get("sources")) > 1
                  ? ` · ${Number(iconHint.feature.get("sources"))} ${plural(
                      Number(iconHint.feature.get("sources")),
                      "источник",
                      "источника",
                      "источников"
                    )}`
                  : ""}
              </span>
            </div>
          ) : null}

          {loading ? <div className="map-loader">Загрузка карты…</div> : null}
          {error ? <div className="map-error">{error}</div> : null}

          <HistoryPanel
            open={historyOpen}
            slots={slots}
            load={slotLoad}
            index={slotIndex}
            playing={playing}
            loading={historyLoading}
            onToggleOpen={() => {
              setHistoryOpen((open) => !open);
              setPlaying(false);
            }}
            days={historyDays}
            selectedDay={selectedDay}
            speed={historySpeed}
            onPickDay={(day) => {
              setSelectedDay(day);
              setPlaying(false);
              if (!day) {
                // Возврат к суточному окну: сбрасываем выгрузку, чтобы
                // эффект перезагрузил последние 24 часа.
                setHistoryEvents(null);
                setSlots([]);
              }
            }}
            onSpeed={setHistorySpeed}
            onSeek={setSlotIndex}
            onTogglePlay={() => setPlaying((value) => !value)}
            onLive={() => {
              setSelectedDay(null);
              setHistoryEvents(null);
              setSlots([]);
              setSlotIndex(0);
              setPlaying(false);
            }}
          />

          <AlertToast
            alerts={alerts}
            onDismiss={() => setAlerts([])}
            onPick={(event) => {
              flyToEvent(event);
              setAlerts([]);
            }}
          />
        </section>

        <button className="map-action" type="button" onClick={() => setAnalyticsOpen(true)}>
          <BarChart3 size={17} aria-hidden="true" />
          <span>Аналитика</span>
        </button>

        <button
          className={`panel-handle handle-right ${rightOpen ? "is-hidden" : ""}`}
          type="button"
          onClick={openRight}
          aria-label="Показать ленту"
        >
          <span className="handle-count">{radarState?.active_events ?? 0}</span>
        </button>

        <FeedPanel
          events={feedEvents}
          collapsed={!rightOpen}
          onCollapse={() => setRightOpen(false)}
          state={radarState}
          apiOnline={apiOnline}
          selectedName={selected.id === "none" || selected.kind === "place" ? null : selected.name}
          selectedZoneId={selectedZoneId}
          zoneEvents={selectedZoneEvents}
          zoneEventsFromRegion={zoneEventsFromRegion}
          regionName={selectedRegionName}
          onlyVisible={onlyVisible}
          onToggleVisible={() => setOnlyVisible((value) => !value)}
          levelFilter={levelFilter}
          onToggleLevel={(level) =>
            setLevelFilter((current) =>
              current.includes(level)
                ? current.filter((item) => item !== level)
                : [...current, level]
            )
          }
          threatFilter={threatFilter}
          onToggleThreat={(threat) =>
            setThreatFilter((current) =>
              current.includes(threat)
                ? current.filter((item) => item !== threat)
                : [...current, threat]
            )
          }
          totalEvents={shownEvents.length}
          bookmarks={bookmarks}
          historyLabel={historyAt ? formatDayTime(historyAt) : null}
          referenceIso={historyAt}
          onClearSelection={() => applySelectedFeature(null)}
          onPickEvent={flyToEvent}
          onToggleBookmark={handleToggleBookmark}
        />
      </main>

      <AnalyticsPanel open={analyticsOpen} onClose={() => setAnalyticsOpen(false)} />
    </div>
  );
}
