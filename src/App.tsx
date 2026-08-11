import { useCallback, useDeferredValue, useEffect, useMemo, useRef, useState } from "react";
import "ol/ol.css";
import OlMap from "ol/Map";
import View from "ol/View";
import GeoJSON from "ol/format/GeoJSON";
import Feature from "ol/Feature";
import LineString from "ol/geom/LineString";
import CircleGeom from "ol/geom/Circle";
import Point from "ol/geom/Point";
import TileLayer from "ol/layer/Tile";
import VectorLayer from "ol/layer/Vector";
import VectorSource from "ol/source/Vector";
import XYZ from "ol/source/XYZ";
import { Attribution, defaults as defaultControls, ScaleLine } from "ol/control";
import { containsCoordinate, createEmpty, extend, getCenter } from "ol/extent";
import { unByKey } from "ol/Observable";
import { fromLonLat, transformExtent } from "ol/proj";
import Style from "ol/style/Style";
import CircleStyle from "ol/style/Circle";
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
import type { RadarEvent, RadarState, RouteLine, SearchItem, ZoneCount, ZoneMeta } from "./lib/api";
import { inferTrails, trailVisibleAt } from "./lib/trails";
import {
  formatAge,
  formatAgo,
  formatDayTime,
  formatDuration,
  numberFormat,
  plural,
  severityColor,
  shortZoneName,
  signalLabel,
  signalWord,
  threatLabel
} from "./lib/format";
import {
  directionArrow,
  iconFreshness,
  iconKindFor,
  iconVisible,
  isPointEvent,
  threatIcon
} from "./lib/icons";
import { zoneFeed } from "./lib/feed";
import { playAlert, playAllClear, setSoundEnabled, soundEnabled } from "./lib/sound";
import { disablePush, enablePush, pushEnabled, pushSupported, syncPushZones } from "./lib/push";
import { telegramWatch } from "./lib/telegram";
import { buildSlots, eventsAt, SLOT_MS, zoneCountsAt } from "./lib/history";
import {
  loadHomeRegion,
  pickStartRegion,
  rememberHomeRegion
} from "./lib/homeRegion";
import { REGION_NEAR_WASH, regionWeight, zoneFillAlpha } from "./lib/paint";
import type { Slot } from "./lib/history";
import type { History, HistoryDay } from "./lib/api";
import {
  loadBookmarks,
  loadSeen,
  markSeen,
  matchBookmarks,
  toggleBookmark
} from "./lib/bookmarks";
import type { Bookmark } from "./lib/bookmarks";
import {
  DETAILED_PLACE_LABEL_ZOOM,
  detailedPlaceLabelMinZoom,
  placeLabelCellKeys
} from "./lib/placeLabels";
import type { DetailedPlaceLabelRow, PlaceLabelManifest } from "./lib/placeLabels";
import { FeedPanel } from "./panels/FeedPanel";
import { HistoryPanel } from "./panels/HistoryPanel";
import { AnalyticsPanel } from "./panels/AnalyticsPanel";
import { AboutPanel } from "./panels/AboutPanel";
import { MobileTabs } from "./panels/MobileTabs";
import type { MobileTab } from "./panels/MobileTabs";
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

type CityLabel = {
  name: string;
  lat: number;
  lon: number;
  population: number;
  /** Код GeoNames: PPLA — центр субъекта, PPLA2 — райцентр. */
  code?: string;
};

type Dataset = {
  regions: GeoJsonFeatureCollection;
  districts: GeoJsonFeatureCollection;
  featuredPlaces: GeoJsonFeatureCollection;
  cityLabels: CityLabel[];
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
  fires: boolean;
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
const FEATURED_PLACES_ZOOM = 7.1;
const FEATURED_PLACE_LABEL_ZOOM = 7.75;
const MAP_MAX_ZOOM = 15.5;
const PLACE_LABELS_FORMAT = 2;
const PLACE_LABEL_CELL_CACHE_LIMIT = 32;
const BASEMAP_URL =
  import.meta.env.VITE_BASEMAP_URL || "https://{a-d}.basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}.png";
const BASEMAP_ATTRIBUTION = import.meta.env.VITE_BASEMAP_ATTRIBUTION || "© OpenStreetMap contributors, © CARTO";

// Подложки на выбор. Тёмная — родная и остаётся по умолчанию: весь
// интерфейс построен под неё. Светлая привычнее людям с бумажных карт,
// спутник отвечает на «а что там вообще на местности». Контуры зон
// читаются на всех трёх: у границ двойной штрих — светлый ореол плюс
// тёмное ядро, — он и был выбран ради независимости от фона.
type BasemapScheme = "dark" | "light" | "satellite";
const BASEMAP_SCHEMES: Record<
  BasemapScheme,
  { label: string; url: string; attributions: string; maxZoom: number;
    hillshade: boolean; opacity: number }
> = {
  dark: {
    label: "Тёмная",
    url: BASEMAP_URL,
    attributions: BASEMAP_ATTRIBUTION,
    maxZoom: 20,
    hillshade: true,
    // Слегка притушена нарочно: фон не должен спорить с событиями.
    opacity: 0.9
  },
  light: {
    label: "Светлая",
    url: "https://{a-d}.basemaps.cartocdn.com/light_nolabels/{z}/{x}/{y}.png",
    attributions: BASEMAP_ATTRIBUTION,
    maxZoom: 20,
    hillshade: true,
    // Полная: под ней чёрный фон приложения, и 0.9 превращали
    // светлую подложку в серую дымку.
    opacity: 1
  },
  satellite: {
    label: "Спутник",
    // Снимки уже несут рельеф — подмешивать hillshade поверх незачем.
    url: "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    attributions: "© Esri, Maxar, Earthstar Geographics",
    maxZoom: 19,
    hillshade: false,
    opacity: 1
  }
};
const BASEMAP_STORE_KEY = "radar.basemap";

function loadBasemapScheme(): BasemapScheme {
  try {
    const saved = localStorage.getItem(BASEMAP_STORE_KEY);
    if (saved === "light" || saved === "satellite") return saved;
  } catch {
    // Приватный режим без localStorage — просто тёмная.
  }
  return "dark";
}
const HILLSHADE_URL =
  import.meta.env.VITE_HILLSHADE_URL || "https://services.arcgisonline.com/ArcGIS/rest/services/Elevation/World_Hillshade/MapServer/tile/{z}/{y}/{x}";
const HILLSHADE_ATTRIBUTION = import.meta.env.VITE_HILLSHADE_ATTRIBUTION || "Tiles © Esri";
// Стартовый вид — европейская часть, где происходит почти вся обстановка.
const OVERVIEW_CENTER = fromLonLat([41, 53.5]);
const DESKTOP_OVERVIEW_ZOOM = 4.15;
// Ширина окна, под которую подобран обзорный масштаб. Шире — считаем ступень
// сами, уже — оставляем как есть: на узком десктопе кадр и так тесный.
const WIDE_REFERENCE_PX = 1500;
// Вертикальный экран узкий, но высокий: зум, подобранный так, чтобы страна
// влезла по ширине, растягивал вид на пол-глобуса по высоте — телефон
// открывался с Африкой и Аравией в кадре, а обстановка занимала треть
// экрана. Держим кадр по европейской части: остальное в паре жестов.
const MOBILE_OVERVIEW_ZOOM = 3.95;
const MAX_SUGGESTIONS = 10;
// Узкий экран — не только про ширину: повёрнутый телефон шире 760 px, но
// высотой в 375, и панели там ведут себя так же, как на вертикальном.
// Тот же список условий продублирован в стилях — держать их врозь нельзя,
// иначе разметка и вёрстка разойдутся, как уже случилось с таб-баром.
const MOBILE_QUERY =
  "(max-width: 760px), (max-height: 500px) and (orientation: landscape)";
const STATE_POLL_MS = 25_000;
// Как часто позволено перезапрашивать полигоны активных районов. Сервер
// держит их в кэше десять секунд, чаще спрашивать нечего.
const ACTIVE_DISTRICTS_MIN_GAP_MS = 20_000;
// Насколько должен сдвинуться курсор, чтобы двигать подсказку. Меньше — это
// дрожание руки, ради которого не стоит перерисовывать приложение.
// Скорость цели для круга возможного положения, м/с. Основа та же, что
// у выцветания: дальнобойный БПЛА самолётной схемы ~150 км/ч; ракета и
// авиация быстрее настолько, что круг честнее не рисовать вовсе — они
// уходят из любого разумного круга за минуты (окно их значка и так
// короткое). БЭК — 0: это надводный катер, а не борт, он ходит только
// по воде. Круг рисуется без разбора берега, и вокруг Новороссийска или
// любого другого порта расползся бы на сушу, куда БЭК выйти не может, —
// карта утверждала бы то, чего физически быть не может. Береговой линии
// у нас нет, честно ограничить круг водой нечем, поэтому для БЭК круга
// нет вовсе, как для ракет и авиации.
const THREAT_SPEED_MPS: Record<string, number> = {
  uav: 41.7,
  fpv: 25,
  bek: 0,
  unknown: 41.7
};
// Меньше двух километров круг неотличим от самого значка.
const RADIUS_MIN_M = 2_000;

function threatSpeedMps(threat: string): number {
  return THREAT_SPEED_MPS[threat] ?? 0;
}

const HINT_MOVE_PX = 6;
// С какого поворота карта считается косой и показывается компас — градус.
// Ниже человек отклонения не видит, а кнопка бы уже мозолила глаз.
const ROTATION_EPSILON = 0.02;


const LAYER_OPTIONS: Array<{ key: keyof LayerState; label: string; swatch: string }> = [
  { key: "basemap", label: "Подложка", swatch: "swatch-basemap" },
  { key: "regions", label: "Регионы", swatch: "swatch-region" },
  { key: "districts", label: "Районы", swatch: "swatch-district" },
  { key: "roads", label: "Дороги", swatch: "swatch-road" },
  { key: "railways", label: "Железные дороги", swatch: "swatch-railway" },
  { key: "urbanAreas", label: "Контуры городов", swatch: "swatch-urban" },
  { key: "waterBodies", label: "Водоемы", swatch: "swatch-water" },
  { key: "rivers", label: "Реки", swatch: "swatch-river" },
  { key: "landCover", label: "Леса и болота", swatch: "swatch-land-cover" },
  // Последствия прилётов видны с орбиты раньше и надёжнее, чем в лентах:
  // НПЗ и склады ГСМ горят сутками. Фоновый слой для интересующихся.
  { key: "fires", label: "Пожары (NASA)", swatch: "swatch-fire" }
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
  // Полигоны приходят мимо API и несут официальное имя зоны целиком.
  const name = shortZoneName(asText(feature.get("name")));
  const zone = feature.get("zone") ? String(feature.get("zone")) : null;

  if (kind === "place") {
    const population = feature.get("population");
    const typeLabel = asText(feature.get("typeLabel"), "Населенный пункт");
    const district = feature.get("district") ? String(feature.get("district")) : null;
    const details: Array<[string, string]> = [["Тип", typeLabel]];
    if (district) details.push(["Район", district]);
    details.push(
      ["Население", typeof population === "number" ? numberFormat.format(population) : "нет данных"],
      ["Координаты", `${asText(feature.get("lat"))}, ${asText(feature.get("lon"))}`]
    );
    return {
      kind,
      id,
      name,
      zone,
      subtitle: district ? `${typeLabel} · ${district}` : typeLabel,
      details
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

const iconStyleCache = new globalThis.Map<string, Style>();
const arrowStyleCache = new globalThis.Map<string, Style>();

function createEventIconStyle(feature: FeatureLike, resolution: number) {
  // Порог ниже стартового масштаба (4.15): иначе значки не видны на загрузке.
  if (resolutionToZoom(resolution) < 3.9) return undefined;

  const kind = String(feature.get("iconKind"));
  const severity = asNumber(feature.get("severity"), 5);
  const ageMs = asNumber(feature.get("ageMs"), 0);
  const threatType = feature.get("threatType");

  // Значок гаснет той же физикой, что и заливка под ним: иначе на бледной
  // зоне стоит яркая метка и спорит с ней. Ступени по десятой, а не по
  // пятой: с крупным шагом двадцать минут и два часа снова слипались бы
  // в одну ступень.
  const freshness = iconFreshness(ageMs, typeof threatType === "string" ? threatType : undefined);
  const bucket = Math.round(freshness * 10) / 10;
  const key = `${kind}|${severity}|${bucket}`;

  let style = iconStyleCache.get(key);
  if (!style) {
    style = new Style({
      image: new Icon({
        src: threatIcon(kind as never, severityColor(severity, 1), bucket),
        scale: 0.62,
        anchor: [0.5, 0.5]
      })
    });
    iconStyleCache.set(key, style);
  }

  // Курс, если лента его назвала: стрелка за краем круга, повёрнутая туда,
  // куда борт идёт. Единственная деталь на карте, отвечающая «на нас?».
  const heading = feature.get("heading");
  if (typeof heading !== "number") return style;

  // Кеш по 15°: непрерывный угол наплодил бы стиль на каждое событие.
  const headingBucket = Math.round(heading / 15) * 15;
  const arrowKey = `${severity}|${bucket}|${headingBucket}`;
  let arrow = arrowStyleCache.get(arrowKey);
  if (!arrow) {
    arrow = new Style({
      image: new Icon({
        src: directionArrow(severityColor(severity, 1), bucket),
        scale: 0.62,
        anchor: [0.5, 0.5],
        rotation: (headingBucket * Math.PI) / 180,
        rotateWithView: true
      })
    });
    arrowStyleCache.set(arrowKey, arrow);
  }
  return [style, arrow];
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

// Ярус появления подписи: миллионники и столицы субъектов видны рано,
// райцентры и десятитысячники приходят вместе с сеткой районов. Числа
// согласованы с порогами карты: районы рисуются с 5.0, их заливка с 5.2.
function cityLabelMinZoom(population: number, code: string): number {
  // Ниже обзорного зума (4.15/3.95): миллионники — ориентиры с первого кадра.
  if (population >= 1_000_000) return 3.6;
  // Столица субъекта — ориентир при любом населении: Магадан и Анадырь
  // важнее на обзоре, чем стотысячник Подмосковья.
  if (code === "PPLA") return 4.6;
  if (population >= 500_000) return 5.0;
  if (population >= 250_000) return 5.6;
  if (population >= 100_000) return 6.2;
  // Райцентр любого размера или город от двадцати тысяч.
  if (code === "PPLA2" || population >= 20_000) return 6.8;
  return 7.4;
}

function createCityLabelStyle(schemeRef: React.MutableRefObject<BasemapScheme>) {
  return (feature: FeatureLike, resolution: number) => {
    const zoom = resolutionToZoom(resolution);
    const population = asNumber(feature.get("population"), 0);
    const code = asText(feature.get("code"), "");
    const detailed = feature.get("detailed") === true;
    const minZoom = detailed
      ? detailedPlaceLabelMinZoom(population, code)
      : cityLabelMinZoom(population, code);
    if (zoom < minZoom) return undefined;
    const major = population >= 1_000_000;
    const minor = detailed && population < 5_000 && !code.startsWith("PPLA");
    // На светлой подложке светлый текст с тёмным ореолом превращается в
    // грязь — цвета меняются местами вместе со схемой.
    const light = schemeRef.current === "light";
    return new Style({
      text: new Text({
        text: asText(feature.get("name"), ""),
        font: `${major ? 600 : minor ? 400 : 500} ${major ? 12.5 : minor ? 10 : 11}px Inter, system-ui, sans-serif`,
        fill: new Fill({
          color: light
            ? `rgba(52, 62, 58, ${minor ? 0.76 : 0.9})`
            : `rgba(225, 232, 228, ${minor ? 0.76 : 0.88})`
        }),
        stroke: new Stroke({
          color: light ? "rgba(255, 255, 255, 0.85)" : "rgba(8, 11, 11, 0.85)",
          width: minor ? 2 : 2.4
        })
      })
    });
  };
}

function createFeaturedPlaceStyle(
  selectedKeyRef: React.MutableRefObject<string | null>
) {
  return (feature: FeatureLike, resolution: number) => {
    const zoom = resolutionToZoom(resolution);
    const selected = selectedKeyRef.current === featureKey(feature);
    const styles = [
      new Style({
        fill: new Fill({
          color: selected ? "rgba(246, 199, 61, 0.1)" : "rgba(150, 164, 157, 0.12)"
        }),
        stroke: new Stroke({
          color: selected ? "rgba(255, 248, 220, 0.92)" : "rgba(194, 206, 200, 0.46)",
          width: selected ? 1.8 : 0.8
        })
      })
    ];

    if (zoom >= FEATURED_PLACE_LABEL_ZOOM) {
      styles.push(
        new Style({
          text: new Text({
            text: asText(feature.get("name"), ""),
            font: "500 11px Inter, system-ui, sans-serif",
            offsetY: String(feature.get("id")) === "519835" ? -12 : 12,
            fill: new Fill({ color: "rgba(225, 232, 228, 0.92)" }),
            stroke: new Stroke({ color: "rgba(8, 12, 11, 0.94)", width: 3.2 }),
            padding: [2, 4, 2, 4]
          })
        })
      );
    }

    return styles;
  };
}

// Обводка выбранной зоны. Рисуется поверх заливки, поэтому вынесена
// отдельно: иначе выбранный район с событиями оставался без границы.
const SELECTION_OUTLINE = new Style({
  stroke: new Stroke({ color: "rgba(255, 248, 220, 0.95)", width: 2.6 })
});

// След налёта в архиве: пунктир, потому что это восстановление по времени
// сообщений, а не заявленный источником путь.
const TRAIL_STYLE = new Style({
  stroke: new Stroke({ color: "rgba(214, 208, 190, 0.42)", width: 1.3, lineDash: [5, 7] })
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

function createUrbanAreaStyle(schemeRef: React.MutableRefObject<BasemapScheme>) {
  const styleCache = new globalThis.Map<string, Style[]>();

  return (feature: FeatureLike, resolution: number) => {
    if (resolutionToZoom(resolution) < asNumber(feature.get("minZoom"), 6)) return undefined;

    const areaSqKm = asNumber(feature.get("areaSqKm"), 0);
    const size = areaSqKm >= 450 ? "large" : areaSqKm >= 120 ? "medium" : "small";
    const scheme = schemeRef.current;
    const cacheKey = `${scheme}:${size}`;
    const cached = styleCache.get(cacheKey);
    if (cached) return cached;

    const coreWidth = size === "large" ? 1.5 : size === "medium" ? 1.3 : 1.05;
    const fillAlpha = size === "large" ? 0.19 : size === "medium" ? 0.15 : 0.11;
    const colors = scheme === "dark"
      ? {
          fill: `rgba(184, 197, 190, ${fillAlpha})`,
          halo: "rgba(4, 8, 7, 0.82)",
          core: "rgba(224, 232, 227, 0.76)"
        }
      : scheme === "light"
        ? {
            fill: `rgba(54, 66, 61, ${fillAlpha})`,
            halo: "rgba(255, 255, 255, 0.92)",
            core: "rgba(46, 57, 52, 0.7)"
          }
        : {
            fill: `rgba(232, 239, 234, ${fillAlpha * 0.8})`,
            halo: "rgba(3, 6, 5, 0.82)",
            core: "rgba(245, 248, 246, 0.86)"
          };

    // Двойная линия сохраняет силуэт на светлых полях, тёмной застройке
    // и спутниковом снимке. Тонкая заливка лишь отделяет город от фона.
    const styles = [
      new Style({
        stroke: new Stroke({ color: colors.halo, width: coreWidth + 2.4 })
      }),
      new Style({
        fill: new Fill({ color: colors.fill }),
        stroke: new Stroke({ color: colors.core, width: coreWidth })
      })
    ];
    styleCache.set(cacheKey, styles);
    return styles;
  };
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
  // Версия формата ответа. Границы кешируются браузером на час, и когда в
  // ответ добавляется новое поле — как случилось с идентификатором зоны, —
  // старый кеш ещё час отдавал бы данные без него, а карта молча не
  // находила бы регион из адреса. Меняется вместе с набором полей — и вместе
  // с геометрией: версия 3 отдаёт прорежённые контуры.
  const GEO_FORMAT = 3;
  const [regions, featuredPlaces, cityLabels] = await Promise.all([
    read(`${API_BASE}/api/v1/geo/regions.geojson?v=${GEO_FORMAT}`).catch(() =>
      read("/data/regions.json")
    ),
    read("/data/featured-places.json").catch(() => ({
      type: "FeatureCollection",
      features: []
    })),
    // Русские имена крупных городов: подложка своих подписей не несёт —
    // латиница CARTO на русской карте обстановки выглядела чужой.
    read("/data/city-labels.json")
      .then((data) => (data.cities ?? []) as CityLabel[])
      .catch(() => [] as CityLabel[])
  ]);

  return {
    regions,
    districts: { type: "FeatureCollection", features: [] },
    featuredPlaces,
    cityLabels
  };
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
  if (window.matchMedia(MOBILE_QUERY).matches) return MOBILE_OVERVIEW_ZOOM;
  // Масштаб задан для окна примерно в полторы тысячи пикселей: при нём в
  // кадре страна и подступы к ней. На широком окне тот же масштаб охватывает
  // вдвое больше — в мини-приложении Telegram, растянутом на весь монитор,
  // в кадр попадали Гренландия и Исландия, а Россия жалась к краю. Каждое
  // удвоение ширины добавляет ступень масштаба, и охват остаётся прежним.
  const width = window.innerWidth || WIDE_REFERENCE_PX;
  if (width <= WIDE_REFERENCE_PX) return DESKTOP_OVERVIEW_ZOOM;
  return DESKTOP_OVERVIEW_ZOOM + Math.log2(width / WIDE_REFERENCE_PX);
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
  const cityLabelLayerRef = useRef<VectorLayer<VectorSource<Feature<Geometry>>> | null>(null);
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
  const featuredPlaceLayerRef = useRef<VectorLayer<VectorSource<Feature<Geometry>>> | null>(null);
  // Состояние зон, ключ — source_id полигона в regions.json / districts.json.
  const zoneStateRef = useRef<globalThis.Map<string, ZoneCount>>(new globalThis.Map());
  const eventIconSourceRef = useRef<VectorSource<Feature<Geometry>>>(new VectorSource());
  // Маршруты из сообщений и следы налёта в архиве: линии живут в своих
  // источниках и пересобираются вместе со значками.
  const routeSourceRef = useRef<VectorSource<Feature<Geometry>>>(new VectorSource());
  const trailSourceRef = useRef<VectorSource<Feature<Geometry>>>(new VectorSource());
  const fireSourceRef = useRef<VectorSource<Feature<Geometry>>>(new VectorSource());
  const fireLayerRef = useRef<VectorLayer<VectorSource<Feature<Geometry>>> | null>(null);
  const firesLoadedRef = useRef(false);
  const eventIconLayerRef = useRef<VectorLayer<VectorSource<Feature<Geometry>>> | null>(null);
  const polygonToZoneRef = useRef<globalThis.Map<string, string>>(new globalThis.Map());
  // Пока стартовый вид не применён, выбора ещё нет — и пустой выбор не
  // должен стереть запомненный регион на первой же отрисовке.
  const homeRegionReadyRef = useRef(false);
  // Обработчик клика по карте создаётся один раз, а состояние листов
  // живёт в React — ref передаёт ему свежее значение. Возвращает true,
  // если лист был открыт и закрыт этим нажатием.
  const sheetGuardRef = useRef<() => boolean>(() => false);
  const selectedKeyRef = useRef<string | null>(null);
  const layersRef = useRef<LayerState | null>(null);
  const loadLazyLayersRef = useRef<(() => void) | null>(null);
  // Подсветка активных районов заказывается по масштабу, а не при старте.
  const loadActiveDistrictsRef = useRef<(() => void) | null>(null);
  const featureIndexRef = useRef<globalThis.Map<string, Feature<Geometry>>>(new globalThis.Map());
  // Выбор, отложенный до подгрузки полигонов. Поиск тихого района на свежей
  // странице раньше просто пролетал мимо: полигоны районов ленивые, и
  // выделять было нечего — человек искал второй раз и решал, что поиск
  // сломан. Ключ формата "district:<source_id>" + срок годности.
  const pendingSelectRef = useRef<{ key: string; until: number } | null>(null);
  const forceDistrictsRef = useRef<(() => void) | null>(null);

  const [dataset, setDataset] = useState<Dataset | null>(null);
  const [selected, setSelected] = useState<SelectedObject>(emptySelected);
  // source_id полигона региона, внутри которого лежит выбранный район.
  const [selectedRegionPolygon, setSelectedRegionPolygon] = useState<string | null>(null);
  // Значок под курсором и его место на экране — для всплывающей подсказки.
  const [iconHint, setIconHint] = useState<{ feature: FeatureLike; pixel: number[] } | null>(null);
  // Регион под курсором в обзорном масштабе. Издалека районов не видно, и
  // человек водит мышью по цветным пятнам, не понимая, что под ними: цвет
  // говорит «тревожно», но не говорит где и что именно.
  const [regionHint, setRegionHint] = useState<
    { name: string; zoneId: string | null; pixel: number[] } | null
  >(null);
  // Поворот карты в радианах. Двумя пальцами его легко задеть, не заметив,
  // и вернуть север было нечем: карта так и оставалась косой.
  const [rotation, setRotation] = useState(0);
  const [radarState, setRadarState] = useState<RadarState | null>(null);
  const [apiOnline, setApiOnline] = useState<boolean | null>(null);
  const [basemapScheme, setBasemapScheme] = useState<BasemapScheme>(loadBasemapScheme);
  const basemapSchemeRef = useRef<BasemapScheme>(basemapScheme);
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
    fires: false,
  });
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const deferredQuery = useDeferredValue(query);

  const [suggestions, setSuggestions] = useState<SearchItem[]>([]);
  const [highlighted, setHighlighted] = useState(0);
  const [bookmarks, setBookmarks] = useState<Bookmark[]>(() => loadBookmarks());
  const [alerts, setAlerts] = useState<RadarEvent[]>([]);
  const [alertSound, setAlertSound] = useState(() => soundEnabled());
  const [pushOn, setPushOn] = useState(() => (pushSupported() ? pushEnabled() : null));
  const [analyticsOpen, setAnalyticsOpen] = useState(false);
  const [aboutOpen, setAboutOpen] = useState(false);
  // Узкий экран отслеживается, а не читается один раз: телефон поворачивают,
  // и в альбомной ориентации панели должны вести себя как на десктопе.
  const [isMobile, setIsMobile] = useState(() => window.matchMedia(MOBILE_QUERY).matches);

  useEffect(() => {
    const media = window.matchMedia(MOBILE_QUERY);
    const sync = () => setIsMobile(media.matches);
    media.addEventListener("change", sync);
    // Плюс обычный resize: во встроенных браузерах — а мини-приложение
    // Telegram именно такой — событие медиазапроса приходит не всегда, и
    // разметка перестраивалась без состояния. Слушать оба источника дешевле,
    // чем разбираться, какой из них сработает у конкретного клиента.
    window.addEventListener("resize", sync);
    return () => {
      media.removeEventListener("change", sync);
      window.removeEventListener("resize", sync);
    };
  }, []);

  // Окно может смениться классом уже после запуска: Telegram на компьютере
  // открывает мини-приложение узким окном, и человек разворачивает его сам.
  // Панели решали свою судьбу один раз при старте — и на широком экране
  // оставались свёрнутыми, а таб-бара, которым их открывают на телефоне,
  // там уже нет. Получалось окно вовсе без ленты и без поиска.
  const wasMobile = useRef(isMobile);
  useEffect(() => {
    if (wasMobile.current === isMobile) return;
    wasMobile.current = isMobile;
    setLeftOpen(!isMobile);
    setRightOpen(!isMobile);

    // Обзорный масштаб подобран под ширину окна: в узком показываем
    // европейскую часть, в широком — страну целиком. Когда окно меняет
    // класс, старый масштаб остаётся, и растянутое окно Telegram на
    // компьютере показывало пол-Земли — Гренландию и Исландию вместо
    // России. Возвращаем обзор, но только если человек сам никуда не
    // уехал: приближение и выбранное место трогать нельзя.
    const map = mapRef.current;
    if (!map) return;
    map.updateSize();
    const zoom = map.getView().getZoom() ?? 0;
    if (zoom <= getOverviewZoom() + 0.35) setOverviewView(map, 250);
  }, [isMobile]);
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
  // На широком экране лента открыта сразу: ради неё сюда и приходят, а
  // открывать её на каждом заходе — лишний шаг. На телефоне наоборот:
  // там она занимает почти весь экран и закрывает карту, ради которой
  // человек и пришёл, — поэтому стартуем с карты, лента в один тап.
  const [rightOpen, setRightOpen] = useState(
    () => !window.matchMedia(MOBILE_QUERY).matches
  );
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyEvents, setHistoryEvents] = useState<RadarEvent[] | null>(null);
  const [historyRoutes, setHistoryRoutes] = useState<RouteLine[] | null>(null);
  const [historyZones, setHistoryZones] = useState<Record<string, ZoneMeta> | null>(null);
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

  // Уже загруженные сутки держим под рукой: человек ходит между соседними
  // днями туда-сюда, и каждый повторный клик стоил полного запроса.
  const dayCacheRef = useRef<globalThis.Map<string, History>>(new globalThis.Map());

  // Выбранные сутки загружаются отдельно от суточного окна: человек мыслит
  // своим днём, а не «последними 24 часами».
  useEffect(() => {
    if (!historyOpen || !selectedDay) return;

    const applyDay = (payload: History) => {
      const built = buildSlots(payload.from, payload.to);
      setHistoryEvents(payload.events);
      setHistoryRoutes(payload.routes ?? []);
      setHistoryZones(payload.zones ?? {});
      setSlots(built);
      setSlotIndex(0);
      setHistoryLoading(false);
    };

    const cached = dayCacheRef.current.get(selectedDay);
    if (cached) {
      applyDay(cached);
      return;
    }

    const controller = new AbortController();
    setHistoryLoading(true);

    api
      .historyDay(selectedDay, controller.signal)
      .then((payload) => {
        if (controller.signal.aborted) return;
        const cache = dayCacheRef.current;
        cache.set(selectedDay, payload);
        // Горячие сутки — это мегабайты; больше восьми держать незачем.
        while (cache.size > 8) {
          const oldest = cache.keys().next().value;
          if (oldest === undefined) break;
          cache.delete(oldest);
        }
        applyDay(payload);
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
      // Диаграмма считает события, по которым в этот срез шли сообщения,
      // а не «ещё не закрытые». Административное правило трёх часов
      // размазывало каждое событие на три лишних столбика, и хвост ночного
      // налёта выглядел как атака прямо сейчас — при пустом эфире.
      const from = at - SLOT_MS;
      let count = 0;
      let severity = 0;
      for (const event of historyEvents) {
        if (new Date(event.first_seen_at).getTime() > at) continue;
        if (new Date(event.last_seen_at).getTime() < from) continue;
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
        setHistoryRoutes(payload.routes ?? []);
        setHistoryZones(payload.zones ?? {});
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
    // Любой явный выбор отменяет отложенный: человек уже передумал.
    pendingSelectRef.current = null;
    selectedKeyRef.current = feature ? featureKey(feature) : null;
    setSelected(feature ? selectedFromFeature(feature) : emptySelected);
    // Регион выбранного района запоминается сразу: если в самом районе
    // сообщений нет, лента показывает обстановку по области — иначе
    // непонятно, почему район вообще закрашен.
    setSelectedRegionPolygon(feature ? regionPolygonOf(feature) : null);
    regionLayerRef.current?.changed();
    districtLayerRef.current?.changed();
    featuredPlaceLayerRef.current?.changed();
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
    //
    // Сокет переподключается сам: раньше первый же обрыв (сон ноутбука,
    // рестарт сервера) навсегда переводил карту на 25-секундный опрос.
    // Пауза растёт вдвое на каждой неудаче и сбрасывается после успешного
    // кадра — упавший сервер не обстреливается, живой ловится за секунды.
    let socket: WebSocket | null = null;
    let retryTimer: number | null = null;
    let retryDelay = 3_000;

    const connect = () => {
      if (!active) return;
      try {
        socket = new WebSocket(`${API_BASE.replace(/^http/, "ws")}/api/v1/stream`);
      } catch {
        // Адрес не годится для сокета вовсе — остаёмся на опросе.
        socket = null;
        return;
      }
      socket.onmessage = (event) => {
        if (!active) return;
        retryDelay = 3_000;
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
      socket.onclose = () => {
        if (!active) return;
        retryTimer = window.setTimeout(connect, retryDelay + Math.random() * 1_000);
        retryDelay = Math.min(retryDelay * 2, 60_000);
      };
      socket.onerror = () => socket?.close();
    };
    connect();

    const timer = window.setInterval(pull, STATE_POLL_MS);
    return () => {
      active = false;
      window.clearInterval(timer);
      if (retryTimer !== null) window.clearTimeout(retryTimer);
      socket?.close();
    };
  }, []);

  const inHistory =
    historyOpen && historyEvents !== null && (selectedDay !== null || slotIndex < slots.length - 1);
  const historyAt = inHistory ? slots[slotIndex]?.at ?? null : null;

  // Карта красится одной и той же формой счётчиков — живой или исторической.
  const paintedZones = useMemo(() => {
    if (inHistory && historyEvents && historyAt) {
      // Метаданные зон приезжают с выгрузкой истории: живые счётчики знают
      // только шумные сейчас зоны, и тихая сегодня зона не красилась в
      // архиве вовсе — значки стояли, а заливки под ними не было.
      return zoneCountsAt(historyEvents, historyAt, {
        ...(radarState?.zone_counts ?? {}),
        ...(historyZones ?? {})
      });
    }
    return radarState?.zone_counts ?? {};
  }, [inHistory, historyEvents, historyAt, historyZones, radarState]);

  const shownEvents = useMemo(() => {
    if (inHistory && historyEvents && historyAt) {
      // Тем же правилом, что и заливка: событие живёт три часа после
      // последнего сообщения, как в конвейере. Прежний фильтр проверял
      // только начало и явный отбой, а отбой есть у меньшинства событий —
      // и фиксация 03:43 стояла значком на карте в 20:00, потому что её
      // никто не «закрыл». Срезов больше нет: карта показывает всё, что
      // было живо в этот момент, предел в шестьдесят — забота ленты.
      return eventsAt(historyEvents, historyAt);
    }
    return radarState?.events ?? [];
  }, [inHistory, historyEvents, historyAt, radarState]);

  // Однозначные звенья налёта из загруженного архива. Считаются один раз
  // на выгрузку: перемотка лишь фильтрует готовый список по моменту.
  const historyTrails = useMemo(
    () => (historyEvents ? inferTrails(historyEvents) : []),
    [historyEvents]
  );

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

      // Окно пролёта вышло — борт зону покинул, и значку «здесь» больше
      // нечего утверждать. Память о событии несёт заливка, не метка.
      const ageMs = Math.max(0, referenceMs - new Date(event.last_seen_at).getTime());
      if (!iconVisible(ageMs, event.threat_type)) continue;

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
          signal: signalLabel(event.signal_type, event.threat_type),
          threat: event.threat_type === "unknown" ? "" : threatLabel(event.threat_type),
          // Сырой тип угрозы — для скорости выцветания значка.
          threatType: event.threat_type,
          sources: event.source_count,
          at: event.last_seen_at,
          ageMs,
          // Разбор хранит, ОТКУДА пришла цель («с юго-запада» — 225).
          // Стрелке нужен курс — куда она идёт дальше, то есть напротив.
          // У отбоя курса не бывает: борта уже нет.
          heading:
            typeof event.direction_deg === "number" && event.signal_type !== "allclear"
              ? (event.direction_deg + 180) % 360
              : null
        })
      );
    }

    // Маршруты, названные самими сообщениями: линия со стрелкой на конце,
    // гаснет тем же окном пролёта, что и значки.
    const routeSource = routeSourceRef.current;
    routeSource.clear();
    const routes = (inHistory ? historyRoutes : radarState?.routes) ?? [];
    for (const route of routes) {
      const age = referenceMs - new Date(route.at).getTime();
      if (age < 0 || !iconVisible(age, route.threat_type)) continue;
      const fresh = iconFreshness(age, route.threat_type);
      const coords = route.points.map((point) => fromLonLat([point[1], point[0]]));

      const line = new Feature({ geometry: new LineString(coords) });
      line.setStyle(
        new Style({
          stroke: new Stroke({
            color: severityColor(route.severity, 0.8 * fresh),
            width: 2.2,
            lineCap: "round"
          })
        })
      );
      routeSource.addFeature(line);

      // Наконечник: та же стрелка, что у курса значков, повёрнутая по
      // последнему плечу маршрута.
      const [x1, y1] = coords[coords.length - 2];
      const [x2, y2] = coords[coords.length - 1];
      const head = new Feature({ geometry: new Point([x2, y2]) });
      head.setStyle(
        new Style({
          image: new Icon({
            src: directionArrow(severityColor(route.severity, 1), fresh),
            scale: 0.62,
            anchor: [0.5, 0.5],
            rotation: Math.atan2(x2 - x1, y2 - y1),
            rotateWithView: true
          })
        })
      );
      routeSource.addFeature(head);
    }

    // Следы налёта — только в архиве и только однозначные звенья: в прямом
    // эфире карта утверждает лишь то, что сказали источники.
    const trailSource = trailSourceRef.current;
    trailSource.clear();

    // Круг возможного положения вокруг свежей фиксации: борт видели в
    // точке N минут назад, и с тех пор он мог уйти на скорость × время.
    // Это не выдумка маршрута, а та же физика ~150 км/ч, из которой
    // выведено выцветание; круг честно говорит «уже может быть здесь» и
    // растёт, пока жив значок. Только для фиксаций: перехват и взрыв —
    // точки свершившегося, им расползаться некуда.
    if (!inHistory) {
      for (const event of shownEvents) {
        if (event.signal_type !== "detection") continue;
        if (typeof event.lat !== "number" || typeof event.lon !== "number") continue;
        const ageMs = Math.max(0, referenceMs - new Date(event.last_seen_at).getTime());
        if (!iconVisible(ageMs, event.threat_type)) continue;
        const radiusM = (ageMs / 1000) * threatSpeedMps(event.threat_type);
        if (radiusM < RADIUS_MIN_M) continue;
        const fresh = iconFreshness(ageMs, event.threat_type);
        const circle = new Feature({
          geometry: new CircleGeom(
            fromLonLat([event.lon, event.lat]),
            // Меркатор растягивает метры с широтой — без поправки круг
            // под Мурманском был бы вдвое меньше заявленного.
            radiusM / Math.cos((event.lat * Math.PI) / 180)
          )
        });
        circle.setStyle(
          new Style({
            fill: new Fill({ color: severityColor(event.severity, 0.045 * fresh) }),
            stroke: new Stroke({
              // Круг всегда лежит на закрашенном районе — том самом, где
              // фиксация. Тонкая красная пунктирная линия по красной
              // заливке давала 9% контраста: замер показал, что круг
              // рисуется верно, но увидеть его нельзя. Ширина и
              // непрозрачность подняты до порога различимости и не выше:
              // это подсказка, а не утверждение о положении борта.
              color: severityColor(event.severity, 0.62 * fresh),
              width: 1.7,
              lineDash: [6, 8]
            })
          })
        );
        trailSource.addFeature(circle);
      }
    }

    if (inHistory && historyAt) {
      const atMs = new Date(historyAt).getTime();
      for (const trail of historyTrails) {
        if (!trailVisibleAt(trail, atMs)) continue;
        const feature = new Feature({
          geometry: new LineString([
            fromLonLat([trail.from[1], trail.from[0]]),
            fromLonLat([trail.to[1], trail.to[0]])
          ])
        });
        feature.setStyle(TRAIL_STYLE);
        trailSource.addFeature(feature);
      }
    }

    const byPolygon = new globalThis.Map<string, string>();
    for (const [zoneId, zone] of Object.entries(radarState?.zone_counts ?? {})) {
      if (zone.source_id) byPolygon.set(zone.source_id, zoneId);
    }
    polygonToZoneRef.current = byPolygon;
    regionLayerRef.current?.changed();
    districtLayerRef.current?.changed();
  }, [paintedZones, radarState, shownEvents, historyAt, inHistory, historyRoutes, historyTrails]);

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

    // С посадочной страницы региона («/region/kurskaya-oblast/») человек
    // приходит с ?region= в адресе и должен сразу увидеть свой субъект
    // выбранным, а не общий вид страны. Без параметра открывается регион,
    // выбранный в прошлый раз: адрес важнее памяти — по ссылке из ленты
    // или с посадочной ждут именно названный регион.
    const wantedRegion = new URLSearchParams(window.location.search).get("region");
    /** true, если регион найден и карта на него наведена. */
    const openWantedRegion = (): boolean => {
      const asked = pickStartRegion(wantedRegion, loadHomeRegion());
      if (!asked) return false;
      const target = regionFeatures.find((feature) => feature.get("zone") === asked);
      // Забытый регион мог исчезнуть из справочника — не держим мёртвый ключ.
      if (!target) {
        if (!wantedRegion) rememberHomeRegion(null);
        return false;
      }
      applySelectedFeature(target);
      // На телефоне выбор не открывает ленту: человек открыл карту и
      // должен увидеть карту своего региона, а не лист поверх неё.
      // Лента — по вкладке «Эфир» или нажатием на зону, когда сам решит.
      if (window.matchMedia(MOBILE_QUERY).matches) setRightOpen(false);
      fitFeature(map, target, 5.2);
      return true;
    };

    const districtFeatures = geoJson.readFeatures(dataset.districts, {
      dataProjection: "EPSG:4326",
      featureProjection: "EPSG:3857"
    }) as Feature<Geometry>[];
    districtFeatures.forEach((feature) => {
      feature.set("kind", "district");
      featureIndexRef.current.set(featureKey(feature), feature);
    });

    const featuredPlaceFeatures = geoJson.readFeatures(dataset.featuredPlaces, {
      dataProjection: "EPSG:4326",
      featureProjection: "EPSG:3857"
    }) as Feature<Geometry>[];
    featuredPlaceFeatures.forEach((feature) => {
      feature.set("kind", "place");
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
    const featuredPlaceSource = new VectorSource({
      features: featuredPlaceFeatures,
      attributions: "© OpenStreetMap contributors"
    });

    const scheme = BASEMAP_SCHEMES[basemapScheme];
    const basemapLayer = new TileLayer({
      source: new XYZ({
        url: scheme.url,
        attributions: scheme.attributions,
        crossOrigin: "anonymous",
        maxZoom: scheme.maxZoom
      }),
      visible: layers.basemap,
      opacity: scheme.opacity,
      zIndex: 0
    });
    const hillshadeLayer = new TileLayer({
      source: new XYZ({
        url: HILLSHADE_URL,
        attributions: HILLSHADE_ATTRIBUTION,
        crossOrigin: "anonymous",
        maxZoom: 13
      }),
      visible: layers.basemap && scheme.hillshade,
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
      zIndex: 26,
      minZoom: 4.3,
      style: createUrbanAreaStyle(basemapSchemeRef)
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

    // Линии под значками: маршрут из сообщения и архивный след. Стили у
    // фич собственные, слоям хватает порядка отрисовки.
    const routeLayer = new VectorLayer({ source: routeSourceRef.current, zIndex: 38 });
    const trailLayer = new VectorLayer({ source: trailSourceRef.current, zIndex: 36 });

    // Пожары — тихий фон под событиями: мелкие тёплые точки, чуть крупнее
    // у мощных очагов. Никаких подписей и кругов — это не сигнал, а контекст.
    const fireLayer = new VectorLayer({
      source: fireSourceRef.current,
      visible: layers.fires,
      zIndex: 35,
      style: (feature) => {
        const frp = asNumber(feature.get("frp"), 5);
        const radius = Math.min(6, 2.4 + Math.log10(Math.max(1, frp)) * 1.6);
        return new Style({
          image: new CircleStyle({
            radius,
            fill: new Fill({ color: "rgba(255, 140, 60, 0.55)" }),
            stroke: new Stroke({ color: "rgba(255, 190, 120, 0.6)", width: 0.8 })
          })
        });
      }
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
    const featuredPlaceLayer = new VectorLayer({
      source: featuredPlaceSource,
      visible: layers.districts,
      zIndex: 30,
      minZoom: FEATURED_PLACES_ZOOM,
      style: createFeaturedPlaceStyle(selectedKeyRef)
    });

    const cityLabelSource = new VectorSource<Feature<Geometry>>({
      attributions: "© GeoNames",
      features: dataset.cityLabels.map((city) => {
        const feature = new Feature({
          geometry: new Point(fromLonLat([city.lon, city.lat])),
          name: city.name,
          population: city.population,
          code: city.code ?? ""
        });
        return feature;
      })
    });
    const cityLabelLayer = new VectorLayer({
      source: cityLabelSource,
      // Подписи должны лежать поверх дорог и административных контуров,
      // но под событиями. Раньше дороги с zIndex 24 перечёркивали текст.
      zIndex: 31,
      declutter: "place-labels",
      style: createCityLabelStyle(basemapSchemeRef)
    });
    cityLabelLayerRef.current = cityLabelLayer;

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
    fireLayerRef.current = fireLayer;
    regionLayerRef.current = regionLayer;
    districtLayerRef.current = districtLayer;
    featuredPlaceLayerRef.current = featuredPlaceLayer;

    const map = new OlMap({
      target: mapNodeRef.current,
      // rotate: false — иначе при повороте карты OpenLayers сам рисует
      // свою кнопку «на север» в правом верхнем углу, чужую по стилю.
      // Наш компас делает то же и появляется там, где его ждёт палец.
      controls: defaultControls({ attribution: false, zoom: false, rotate: false }).extend([
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
        cityLabelLayer,
        districtLayer,
        featuredPlaceLayer,
        fireLayer,
        trailLayer,
        routeLayer,
        eventIconLayer
      ],
      view: new View({
        center: OVERVIEW_CENTER,
        zoom: getOverviewZoom(),
        minZoom: 1.75,
        maxZoom: MAP_MAX_ZOOM
      })
    });

    // Контейнер позиционирован абсолютно, и на момент создания его размер
    // может быть ещё нулевым — OpenLayers запомнит ноль и больше ничего не
    // нарисует. Стартовый вид тоже нельзя задавать раньше: выбор десктопного
    // или мобильного масштаба зависит от фактической ширины.
    let viewApplied = false;
    // Стартовый вид: обзор страны или регион — из адреса, если человек
    // пришёл с посадочной страницы, иначе запомненный свой. Обе ветки идут
    // здесь, а не раньше: до появления размера карта не умеет ни
    // центрироваться, ни подлетать к границам, и вид молча оставался бы
    // обзорным.
    const applyStartView = () => {
      if (!openWantedRegion()) setOverviewView(map, 0);
      homeRegionReadyRef.current = true;
    };
    const resizeObserver = new ResizeObserver(() => {
      map.updateSize();
      const [width, height] = map.getSize() ?? [0, 0];
      if (width > 0 && height > 0) {
        if (!viewApplied) {
          viewApplied = true;
          applyStartView();
        }
        setViewExtent(map.getView().calculateExtent([width, height]));
      }
    });
    resizeObserver.observe(mapNodeRef.current);
    map.updateSize();

    if ((map.getSize()?.[0] ?? 0) > 0) {
      viewApplied = true;
      applyStartView();
    }
    let disposed = false;
    let detailedPlaceRequest = 0;
    let renderedDetailedPlaceFeatures: Feature<Geometry>[] = [];
    let placeLabelManifestPromise: Promise<PlaceLabelManifest | null> | null = null;
    let placeLabelCells: ReadonlySet<string> = new Set();
    const placeLabelCellCache = new globalThis.Map<
      string,
      Promise<DetailedPlaceLabelRow[]>
    >();

    const clearDetailedPlaceLabels = () => {
      if (!renderedDetailedPlaceFeatures.length) return;
      cityLabelSource.removeFeatures(renderedDetailedPlaceFeatures);
      renderedDetailedPlaceFeatures = [];
    };

    const loadPlaceLabelManifest = () => {
      if (placeLabelManifestPromise) return placeLabelManifestPromise;
      placeLabelManifestPromise = fetch(
        `/data/place-labels/manifest.json?v=${PLACE_LABELS_FORMAT}`
      )
        .then(async (response) => {
          if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
          const manifest = (await response.json()) as PlaceLabelManifest;
          if (
            manifest.version !== PLACE_LABELS_FORMAT ||
            !Number.isFinite(manifest.cellSize) ||
            manifest.cellSize <= 0 ||
            !Array.isArray(manifest.cells)
          ) {
            throw new Error("неизвестный формат подписей");
          }
          placeLabelCells = new Set(manifest.cells);
          return manifest;
        })
        .catch(() => {
          placeLabelManifestPromise = null;
          return null;
        });
      return placeLabelManifestPromise;
    };

    const loadPlaceLabelCell = (
      key: string,
      version: number
    ): Promise<DetailedPlaceLabelRow[]> => {
      const cached = placeLabelCellCache.get(key);
      if (cached) {
        // Map хранит порядок вставки: повторно посещённая клетка становится
        // самой свежей и не вылетает первой из небольшого LRU-кеша.
        placeLabelCellCache.delete(key);
        placeLabelCellCache.set(key, cached);
        return cached;
      }
      const request = fetch(`/data/place-labels/${key}.json?v=${version}`)
        .then(async (response) => {
          if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
          const rows = await response.json();
          return Array.isArray(rows) ? rows as DetailedPlaceLabelRow[] : [];
        })
        .catch(() => {
          if (placeLabelCellCache.get(key) === request) {
            placeLabelCellCache.delete(key);
          }
          return [];
        });
      placeLabelCellCache.set(key, request);
      while (placeLabelCellCache.size > PLACE_LABEL_CELL_CACHE_LIMIT) {
        const oldest = placeLabelCellCache.keys().next().value;
        if (typeof oldest !== "string") break;
        placeLabelCellCache.delete(oldest);
      }
      return request;
    };

    const syncDetailedPlaceLabels = async () => {
      const requestId = ++detailedPlaceRequest;
      const zoom = map.getView().getZoom() ?? 0;
      if (zoom < DETAILED_PLACE_LABEL_ZOOM) {
        clearDetailedPlaceLabels();
        return;
      }

      const size = map.getSize();
      if (!size || size[0] <= 0 || size[1] <= 0) return;
      const manifest = await loadPlaceLabelManifest();
      if (!manifest || disposed || requestId !== detailedPlaceRequest) return;

      const lonLatExtent = transformExtent(
        map.getView().calculateExtent(size),
        "EPSG:3857",
        "EPSG:4326"
      );
      const keys = placeLabelCellKeys(lonLatExtent, manifest.cellSize, placeLabelCells);
      const rowsByCell = await Promise.all(
        keys.map((key) => loadPlaceLabelCell(key, manifest.version))
      );
      if (disposed || requestId !== detailedPlaceRequest) return;

      const features: Feature<Geometry>[] = [];
      for (const rows of rowsByCell) {
        for (const row of rows) {
          if (!Array.isArray(row) || row.length < 5) continue;
          const [name, lat, lon, rawPopulation, code] = row;
          const population = asNumber(rawPopulation, 0);
          if (
            typeof name !== "string" ||
            typeof code !== "string" ||
            !Number.isFinite(lat) ||
            !Number.isFinite(lon) ||
            zoom < detailedPlaceLabelMinZoom(population, code)
          ) {
            continue;
          }
          features.push(new Feature({
            geometry: new Point(fromLonLat([lon, lat])),
            name,
            population,
            code,
            detailed: true
          }));
        }
      }

      clearDetailedPlaceLabels();
      cityLabelSource.addFeatures(features);
      renderedDetailedPlaceFeatures = features;
    };

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
        if (source === districtSource) {
          districtLayerRef.current?.changed();
          applyPendingSelection();
        }
      } catch (reason: unknown) {
        loadedRef.current = false;
        if (!disposed) {
          setError(reason instanceof Error ? `Не удалось загрузить слой ${layerName}: ${reason.message}` : `Не удалось загрузить слой ${layerName}`);
        }
      }
    };

    // Выделить место, которое поиск выбрал раньше, чем приехал его полигон.
    // Ключ снимается до применения: applySelectedFeature сама чистит
    // отложенный выбор, и иначе она стёрла бы то, что мы как раз применяем.
    const applyPendingSelection = () => {
      const pending = pendingSelectRef.current;
      if (!pending) return;
      if (Date.now() > pending.until) {
        pendingSelectRef.current = null;
        return;
      }
      const feature = featureIndexRef.current.get(pending.key);
      if (!feature) return;
      pendingSelectRef.current = null;
      applySelectedFeature(feature);
      fitFeature(map, feature, 7.4);
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
      // Подсветка активных районов нужна с того же масштаба, с которого их
      // видно. Ссылка, а не прямой вызов: обработчик объявлен ниже.
      loadActiveDistrictsRef.current?.();
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
        applyPendingSelection();
      } catch {
        // Подсветка появится, когда подгрузится полный набор районов.
      }
    };

    // Полигоны активных районов — 76 КБ поверх всего, что и так грузится при
    // открытии. На стартовом масштабе слой районов не рисуется вовсе
    // (minZoom у него 4.15), так что первые секунды эти килобайты заняты
    // ничем — а на мобильном интернете именно они и есть та разница между
    // «карта появилась» и «страница висит». Ждём, пока районы понадобятся.
    const districtsNeeded = () =>
      (mapRef.current?.getView().getZoom() ?? 0) >= DISTRICT_FILL_ZOOM - 1;
    // Масштаб меняется на каждый щелчок колеса, а подсветка столько раз не
    // обновляется: без этой заслонки одно приближение стоило бы трёх-четырёх
    // запросов по 76 КБ подряд.
    let districtsAskedAt = 0;
    const loadActiveDistrictsWhenNeeded = () => {
      const now = Date.now();
      if (!districtsNeeded() || now - districtsAskedAt < ACTIVE_DISTRICTS_MIN_GAP_MS) return;
      districtsAskedAt = now;
      void loadActiveDistricts();
    };
    loadActiveDistrictsRef.current = loadActiveDistrictsWhenNeeded;
    loadActiveDistrictsWhenNeeded();
    const activeDistrictsTimer = window.setInterval(loadActiveDistrictsWhenNeeded, 60_000);

    loadLazyLayersRef.current = maybeLoadLazyLayers;
    // Поиску нужен способ дозаказать полигоны районов, не дожидаясь зума.
    forceDistrictsRef.current = () =>
      void loadVectorLayer("/data/districts.json", districtSource, districtsLoadedRef, "районов");
    const syncExtent = () => {
      const size = map.getSize();
      if (size && size[0] > 0 && size[1] > 0) {
        setViewExtent(map.getView().calculateExtent(size));
      }
    };
    const syncMapView = () => {
      syncExtent();
      void syncDetailedPlaceLabels();
    };
    map.on("moveend", syncMapView);
    syncMapView();

    const viewResolutionKey = map.getView().on("change:resolution", () => {
      maybeLoadLazyLayers();
      regionLayerRef.current?.changed();
      districtLayerRef.current?.changed();
      featuredPlaceLayerRef.current?.changed();
    });
    maybeLoadLazyLayers();

    // Кнопка возврата на север появляется только при повороте, поэтому его
    // и слушаем. Событие редкое: жест поворота — не панорама.
    map.getView().on("change:rotation", () => {
      setRotation(map.getView().getRotation());
    });

    // Запрос создаётся один раз: на каждое движение указателя он и сам
    // обошёлся бы дороже, чем стоит ответ.
    const narrowScreen = window.matchMedia(MOBILE_QUERY);

    map.on("pointermove", (event) => {
      // Три прохода поиска фичи под курсором — это три скрытых отрисовки
      // карты с чтением пикселей, и каждая подсказка вдобавок перерисовывает
      // всё приложение. Пока карту тащат, ответ на вопрос «что под курсором»
      // никому не нужен, а событий движения там больше всего: телефоны от
      // этого грелись, ничего не показывая.
      if (event.dragging) return;
      // На узком экране наводить нечем: обе подсказки скрыты стилями, но
      // считались всё равно — палец, ведущий карту, платил за них полностью.
      if (narrowScreen.matches) return;

      const hit = map.hasFeatureAtPixel(event.pixel, {
        hitTolerance: 5,
        layerFilter: (layer) =>
          layer === regionLayer || layer === districtLayer || layer === featuredPlaceLayer
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
      setIconHint((previous) => {
        if (!icon) return previous === null ? previous : null;
        return previous && previous.feature === icon
          && Math.abs(previous.pixel[0] - event.pixel[0]) < HINT_MOVE_PX
          && Math.abs(previous.pixel[1] - event.pixel[1]) < HINT_MOVE_PX
          ? previous
          : { feature: icon, pixel: event.pixel };
      });
      if (icon && target) target.style.cursor = "pointer";

      // Издалека карта красит регионы целиком, и понять, что за пятно под
      // курсором, было нельзя. Подсказка называет субъект и говорит, что в
      // нём сейчас. Подсказке значка она не мешает: та подробнее и главнее.
      if (icon || resolutionToZoom(map.getView().getResolution() ?? 1) >= DISTRICT_FILL_ZOOM) {
        setRegionHint(null);
        return;
      }
      let region: FeatureLike | null = null;
      map.forEachFeatureAtPixel(
        event.pixel,
        (feature) => { region = feature; return true; },
        { hitTolerance: 0, layerFilter: (layer) => layer === regionLayer }
      );
      if (!region) {
        setRegionHint(null);
        return;
      }
      const found = region as FeatureLike;
      const sourceId = String(found.get("id") ?? "");
      const name = shortZoneName(asText(found.get("name")));
      const zoneId = polygonToZoneRef.current.get(sourceId) ?? null;
      // Пока курсор ходит по тому же региону, менять нечего: новый объект
      // состояния перерисовывал бы всё приложение — вместе с лентой из
      // шестидесяти событий — на каждый пиксель движения мыши.
      setRegionHint((previous) =>
        previous && previous.zoneId === zoneId && previous.name === name
          && Math.abs(previous.pixel[0] - event.pixel[0]) < HINT_MOVE_PX
          && Math.abs(previous.pixel[1] - event.pixel[1]) < HINT_MOVE_PX
          ? previous
          : { name, zoneId, pixel: event.pixel }
      );
    });

    map.on("singleclick", (event) => {
      // Открытый лист на телефоне закрывается нажатием на карту — так
      // тянется рука: «убери это меню». Без гварда тап по карте выбирал
      // регион под пальцем, а выбор снова открывал ленту — из листа
      // нельзя было выйти нажатием туда, куда смотришь. Выбор места этим
      // нажатием не делается: сначала вернуть карту, потом выбирать.
      // История — не лист, а плеер: ею пользуются вместе с картой.
      if (narrowScreen.matches && sheetGuardRef.current()) return;
      const zoom = map.getView().getZoom() ?? 0;
      let regionFeature: FeatureLike | null = null;
      let districtFeature: FeatureLike | null = null;
      let placeFeature: FeatureLike | null = null;

      map.forEachFeatureAtPixel(
        event.pixel,
        (feature, layer) => {
          if (layer === featuredPlaceLayer && !placeFeature) {
            placeFeature = feature;
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
          layerFilter: (layer) =>
            layer === featuredPlaceLayer || layer === districtLayer || layer === regionLayer
        }
      );

      const picked =
        placeFeature ??
        (zoom >= DISTRICT_SELECTION_ZOOM
          ? districtFeature ?? regionFeature
          : regionFeature ?? districtFeature);

      // Повторное нажатие по уже выбранному месту снимает выбор: иначе
      // выйти из него можно было только крестиком в ленте, а рука тянется
      // ткнуть туда же ещё раз.
      const same = picked && featureKey(picked) === selectedKeyRef.current;
      applySelectedFeature(same ? null : picked);
    });

    mapRef.current = map;

    return () => {
      disposed = true;
      detailedPlaceRequest += 1;
      window.clearInterval(activeDistrictsTimer);
      resizeObserver.disconnect();
      unByKey(viewResolutionKey);
      map.un("moveend", syncMapView);
      clearDetailedPlaceLabels();
      map.setTarget(undefined);
      mapRef.current = null;
      basemapLayerRef.current = null;
      hillshadeLayerRef.current = null;
      cityLabelLayerRef.current = null;
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
      forceDistrictsRef.current = null;
      pendingSelectRef.current = null;
      layersRef.current = null;
      eventIconLayerRef.current = null;
      regionLayerRef.current = null;
      districtLayerRef.current = null;
      featuredPlaceLayerRef.current = null;
      featureIndexRef.current.clear();
    };
  }, [applySelectedFeature, dataset]);

  useEffect(() => {
    layersRef.current = layers;
    basemapLayerRef.current?.setVisible(layers.basemap);
    hillshadeLayerRef.current?.setVisible(
      layers.basemap && BASEMAP_SCHEMES[basemapScheme].hillshade
    );
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
    featuredPlaceLayerRef.current?.setVisible(layers.districts);
    fireLayerRef.current?.setVisible(layers.fires);
    loadLazyLayersRef.current?.();

    // Точки пожаров грузятся при первом включении слоя, а не на старте:
    // выключенный слой не должен стоить ни одного запроса.
    if (layers.fires && !firesLoadedRef.current) {
      firesLoadedRef.current = true;
      void api
        .fires()
        .then((payload) => {
          const source = fireSourceRef.current;
          source.clear();
          for (const [lat, lon, frp] of payload.points) {
            source.addFeature(
              new Feature({
                geometry: new Point(fromLonLat([lon, lat])),
                kind: "fire",
                frp
              })
            );
          }
        })
        .catch(() => {
          firesLoadedRef.current = false;
        });
    }
  }, [layers, basemapScheme]);

  // Смена подложки: новый источник в тот же слой, карта не пересоздаётся.
  useEffect(() => {
    const scheme = BASEMAP_SCHEMES[basemapScheme];
    basemapLayerRef.current?.setSource(
      new XYZ({
        url: scheme.url,
        attributions: scheme.attributions,
        crossOrigin: "anonymous",
        maxZoom: scheme.maxZoom
      })
    );
    basemapLayerRef.current?.setOpacity(scheme.opacity);
    basemapSchemeRef.current = basemapScheme;
    urbanAreaLayerRef.current?.changed();
    cityLabelLayerRef.current?.changed();
    try {
      localStorage.setItem(BASEMAP_STORE_KEY, basemapScheme);
    } catch {
      // Приватный режим — выбор живёт до перезагрузки.
    }
  }, [basemapScheme]);

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

  // Регионы без единого события. Всего субъектов в справочнике 89; из
  // paintedZones берём только уровень региона.
  const quietRegions = useMemo(() => {
    if (!radarState) return null;
    const litRegions = Object.values(paintedZones).filter(
      (zone) => zone.level === "region"
    ).length;
    const total = dataset?.regions.features.length ?? 0;
    if (!total) return null;
    return Math.max(0, total - litRegions);
  }, [radarState, paintedZones, dataset]);

  const selectedRegionName = useMemo(() => {
    if (!selectedRegionPolygon) return null;
    // Имя берётся из самого полигона региона: счётчики обстановки знают
    // только шумные зоны, и у тихого региона имени в них нет — а карточка
    // обязана называть регион всегда, не только когда в нём что-то горит.
    const feature = featureIndexRef.current.get(`region:${selectedRegionPolygon}`);
    const name = feature?.get("name");
    if (name) return shortZoneName(String(name));
    const zoneId = polygonToZoneRef.current.get(selectedRegionPolygon);
    return zoneId ? paintedZones[zoneId]?.name ?? null : null;
  }, [selectedRegionPolygon, paintedZones]);

  // Свой регион запоминается сам: специально ничего отмечать не надо, а
  // чтобы забыть — достаточно снять выделение нажатием мимо. Зона берётся
  // из полигона, а не из счётчиков обстановки: у тихого региона счётчика
  // нет, и запоминалась бы только область, где что-то происходит.
  useEffect(() => {
    if (!homeRegionReadyRef.current) return;
    if (!selectedRegionPolygon) {
      rememberHomeRegion(null);
      return;
    }
    const zone = featureIndexRef.current
      .get(`region:${selectedRegionPolygon}`)?.get("zone");
    if (zone) rememberHomeRegion(String(zone));
  }, [selectedRegionPolygon]);

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

      // На телефоне выбранная подсказка закрывает лист поиска: карта
      // летит к месту, и человек должен это видеть. Раньше ветка НП без
      // полигона оставляла лист открытым — полёт происходил за ним.
      if (window.matchMedia(MOBILE_QUERY).matches) setLeftOpen(false);

      // Для объектов с загруженным контуром подлетаем к границам, а не к
      // условной центральной точке. Большинство населённых пунктов остаются
      // точечными; точные контуры пока есть у приоритетных объектов.
      if (item.source_id) {
        const feature = featureIndexRef.current.get(`${item.level}:${item.source_id}`);
        if (feature) {
          applySelectedFeature(feature);
          fitFeature(
            map,
            feature,
            item.level === "region" ? 5.2 : item.level === "district" ? 7.4 : 9.15
          );
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
          zoom: Math.max(
            map.getView().getZoom() ?? 5,
            item.level === "place" ? 11 : item.level === "district" ? 7 : 5.2
          ),
          duration: 420
        });
      }

      // Полигоны районов ленивые, и на свежей странице выделять ещё нечего:
      // поиск находил район, карта долетала до места — и ничего не
      // подсвечивалось, пока не поищешь второй раз. Просим полный набор и
      // откладываем выделение до его прихода. Ставится после
      // applySelectedFeature(null): та чистит отложенный выбор.
      if (item.source_id && item.level === "district") {
        pendingSelectRef.current = {
          key: `district:${item.source_id}`,
          until: Date.now() + 20_000
        };
        forceDistrictsRef.current?.();
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

  // Уведомление по отслеживаемым местам — один раз на событие. Отбой
  // приходит тем же путём со своим ключом «id:clear»: человеку он важнее
  // самой тревоги, а раньше тревога просто молча гасла.
  useEffect(() => {
    if (!radarState || !bookmarks.length || historyAt) return;
    const seen = new Set(loadSeen());
    const matched = matchBookmarks(radarState.events, bookmarks);
    const alarms = matched.filter(
      (event) => event.status !== "resolved" && !seen.has(event.id)
    );
    const clears = matched.filter(
      (event) => event.status === "resolved" && !seen.has(`${event.id}:clear`)
    );
    if (!alarms.length && !clears.length) return;
    setAlerts([...alarms, ...clears]);
    // Звук — только по явному выбору: тема не та, где сюрпризы уместны.
    // Отбой звучит мягче и вниз; при одновременной тревоге громче она.
    if (alertSound) {
      if (alarms.length) playAlert();
      else playAllClear();
    }
    markSeen([
      ...alarms.map((event) => event.id),
      ...clears.map((event) => `${event.id}:clear`)
    ]);
  }, [radarState, bookmarks, historyAt, alertSound]);

  const resetMap = useCallback(() => {
    const map = mapRef.current;
    if (!map) return;
    setOverviewView(map, 420);
    applySelectedFeature(null);
    setQuery("");
  }, [applySelectedFeature]);

  // Свежая версия для обработчика клика карты (он создан один раз).
  sheetGuardRef.current = () => {
    if (!leftOpen && !rightOpen) return false;
    setLeftOpen(false);
    setRightOpen(false);
    return true;
  };

  // Какая вкладка подсвечена внизу на телефоне. Панель открыта ровно одна,
  // поэтому вкладка выводится из состояния панелей, а не хранится отдельно:
  // иначе они разошлись бы при закрытии панели крестиком.
  const mobileTab: MobileTab = analyticsOpen
    ? "analytics"
    : historyOpen
      ? "history"
      : leftOpen
        ? "search"
        : rightOpen
          ? "feed"
          : "map";

  const pickMobileTab = useCallback((tab: MobileTab) => {
    // Любой выбор сначала закрывает всё: на узком экране две панели разом
    // не помещаются, а «Карта» — это просто пустой набор.
    setLeftOpen(false);
    setRightOpen(false);
    setHistoryOpen(false);
    setAnalyticsOpen(false);
    if (tab === "search") setLeftOpen(true);
    if (tab === "feed") setRightOpen(true);
    if (tab === "history") setHistoryOpen(true);
    if (tab === "analytics") setAnalyticsOpen(true);
    if (tab === "map") setPlaying(false);
  }, []);

  // Пуш включается явным жестом (браузер спросит разрешение), а дальше
  // список зон уезжает на сервер при каждом изменении закладок.
  const togglePush = useCallback(() => {
    if (pushOn === null) return;
    if (pushOn) {
      setPushOn(false);
      void disablePush();
      return;
    }
    enablePush(bookmarks.map((item) => item.zone_id))
      .then(() => setPushOn(true))
      .catch(() => setPushOn(false));
  }, [pushOn, bookmarks]);

  useEffect(() => {
    if (!pushOn) return;
    void syncPushZones(bookmarks.map((item) => item.zone_id));
  }, [pushOn, bookmarks]);

  // В мини-аппе Telegram колокольчик подписывает на уведомления в чат:
  // дельта закладок уходит боту как /watch и /unwatch. Начальная загрузка
  // дельты не даёт — ссылка стартует с того же списка.
  const bookmarksBefore = useRef(bookmarks);
  useEffect(() => {
    const before = bookmarksBefore.current;
    bookmarksBefore.current = bookmarks;
    if (before === bookmarks) return;
    const was = new Set(before.map((item) => item.zone_id));
    const now = new Set(bookmarks.map((item) => item.zone_id));
    for (const zoneId of now) if (!was.has(zoneId)) telegramWatch(zoneId, true);
    for (const zoneId of was) if (!now.has(zoneId)) telegramWatch(zoneId, false);
  }, [bookmarks]);

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

        {/* На телефоне счётчики из шапки убраны совсем — не спрятаны в
            бейдж вкладки, а именно убраны: владелец попросил дважды, второй
            раз явно («никому не нужно... вовсе убрать»), после того как
            первая попытка подменила счётчик бейджем на вкладке «Эфир» —
            это тоже было число, только в другом месте. Разбивка по типам
            живёт в «Сводке», кто хочет — откроет сам. В Telegram стрип
            вдобавок толкался с кнопками самого мессенджера и выглядел
            сломанным. Исключение — архив: пометку «показана история» карта
            обязана нести всегда, это не украшение, а предупреждение, что
            перед глазами не текущая обстановка. */}
        {!isMobile || historyAt ? (
          <TopbarStats
            events={radarState ? shownEvents : null}
            zones={Object.keys(paintedZones).length}
            quietRegions={quietRegions}
            historyLabel={historyAt ? formatDayTime(historyAt) : null}
            moment={radarState?.generated_at ?? null}
          />
        ) : null}

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
              <span data-tip="Борт уже видят: фиксация, перехват, взрыв. Это про то, ЧТО сообщили, а не про то, сколько лент это подтвердило">
                <i style={{ background: severityColor(9, 0.95) }} aria-hidden="true" />Фиксация, перехват, взрыв
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
            pushOn={pushOn}
            onTogglePush={togglePush}
            soundOn={alertSound}
            onToggleSound={() => {
              const next = !alertSound;
              setAlertSound(next);
              setSoundEnabled(next);
              // Пробный сигнал при включении: слышно, что именно включил,
              // и заодно браузер получает жест для разрешения звука.
              if (next) playAlert();
            }}
          />

          <details className="extra-layers">
            <summary>
              <Layers size={16} aria-hidden="true" />
              <span>Слои карты</span>
            </summary>
            <div className="layer-list">
              <div className="basemap-switch" role="radiogroup" aria-label="Подложка карты">
                {(Object.keys(BASEMAP_SCHEMES) as BasemapScheme[]).map((key) => (
                  <button
                    key={key}
                    type="button"
                    className={basemapScheme === key ? "is-active" : ""}
                    aria-pressed={basemapScheme === key}
                    onClick={() => setBasemapScheme(key)}
                  >
                    {BASEMAP_SCHEMES[key].label}
                  </button>
                ))}
              </div>
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
            <button
              className="about-link"
              type="button"
              onClick={() => setAboutOpen(true)}
            >
              Как это работает
            </button>
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
                {/* Свежая метка отвечает «когда», а не «во сколько»:
                    «3 минуты назад» читается сразу, «19:13» приходится
                    вычитать из текущего времени в уме. */}
                {formatAgo(
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

          {/* Подсказка региона в обзорном масштабе: цвет пятна говорит
              «тревожно», но не говорит, чей это регион и что там. */}
          {regionHint ? (
            <div
              className="icon-hint region-hint"
              style={{ left: regionHint.pixel[0], top: regionHint.pixel[1] }}
              role="tooltip"
            >
              <b>{regionHint.name}</b>
              {(() => {
                const zone = regionHint.zoneId ? paintedZones[regionHint.zoneId] : undefined;
                if (!zone || !zone.active) {
                  return <span className="region-hint-quiet">сообщений нет</span>;
                }
                return (
                  <>
                    <span>
                      <i
                        className="chip-dot"
                        style={{ background: severityColor(zone.severity, 0.95) }}
                        aria-hidden="true"
                      />
                      {signalWord(zone.severity)} · {zone.active}{" "}
                      {plural(zone.active, "сообщение", "сообщения", "сообщений")}
                    </span>
                    <span className="icon-hint-foot">
                      {formatAgo(
                        zone.last_active,
                        historyAt ?? radarState?.generated_at ?? new Date().toISOString()
                      )}
                    </span>
                  </>
                );
              })()}
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
                setHistoryRoutes(null);
                setHistoryZones(null);
                setSlots([]);
              }
            }}
            onSpeed={setHistorySpeed}
            onSeek={setSlotIndex}
            onTogglePlay={() => setPlaying((value) => !value)}
            onLive={() => {
              setSelectedDay(null);
              setHistoryEvents(null);
              setHistoryRoutes(null);
              setHistoryZones(null);
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

        {/* Компас показывается, только когда карта повёрнута: на ровной карте
            кнопка «вернуть север» ничего не значит и место занимает зря.
            Стрелка отклоняется вместе с картой и всегда смотрит на север —
            по ней и видно, насколько вид развёрнут. */}
        {Math.abs(rotation) > ROTATION_EPSILON ? (
          <button
            className="compass-button"
            type="button"
            onClick={() => {
              mapRef.current?.getView().animate({ rotation: 0, duration: 250 });
            }}
            title="Повернуть карту на север"
            aria-label="Повернуть карту на север"
          >
            {/* Знак тот же, что у вида: положительный поворот в OpenLayers
                уводит север вправо, туда же должна смотреть и стрелка. */}
            <svg viewBox="0 0 24 24" aria-hidden="true"
                 style={{ transform: `rotate(${rotation}rad)` }}>
              <path d="M12 3 L16 20 L12 16.4 L8 20 Z" fill="currentColor" />
            </svg>
          </button>
        ) : null}

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

      {/* На телефоне вместо плавающих ярлыков по углам — одна нижняя
          навигация: они там наезжали друг на друга и на края экрана. */}
      {isMobile ? (
        <MobileTabs active={mobileTab} onPick={pickMobileTab} />
      ) : null}

      <AnalyticsPanel open={analyticsOpen} onClose={() => setAnalyticsOpen(false)} />
      <AboutPanel open={aboutOpen} onClose={() => setAboutOpen(false)} />
    </div>
  );
}
