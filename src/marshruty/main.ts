/**
 * Карта коридоров на странице /marshruty/.
 *
 * Тот же движок и та же тайловая подложка, что у живой карты. Данные —
 * dist/data/corridors.json (пересобирается ежечасно): готовые ТРАССЫ,
 * склеенные сервером из многих плеч в длинные линии, с морскими дугами
 * на прибрежных участках. Здесь только отрисовка: толщина по весу,
 * бегущие штрихи по направлению, подписи путевых точек (declutter),
 * подсказки и переход к карточке коридора.
 */

import "ol/ol.css";
import OlMap from "ol/Map";
import View from "ol/View";
import TileLayer from "ol/layer/Tile";
import VectorLayer from "ol/layer/Vector";
import VectorSource from "ol/source/Vector";
import XYZ from "ol/source/XYZ";
import Feature from "ol/Feature";
import LineString from "ol/geom/LineString";
import Point from "ol/geom/Point";
import { fromLonLat, transformExtent } from "ol/proj";
import { Fill, Stroke, Style, Text } from "ol/style";
import type { FeatureLike } from "ol/Feature";
import { defaults as defaultControls } from "ol/control";

const BASEMAP_URL =
  "https://{a-d}.basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}.png";
const ATTRIBUTION = "© OpenStreetMap contributors, © CARTO";

type Chain = {
  pts: [number, number][];
  from: string; to: string; via: string[];
  n: number; nm: number; cp: number; r: number;
  s: number; t: number; cs: number; kor?: string;
};
type MapLabel = { lat: number; lon: number; name: string; t: number };
type Graph = { chains: Chain[]; labels: MapLabel[] };

// Красное — пересказ источника, жёлтое — собранное по нашим волнам
// фиксаций. Цвет отвечает на вопрос «чьё это знание», и потому же
// подсказка всегда называет доли.
const TRUNK = "#f0475a";
const LOCAL = "#c33b49";
const ANT = "#ffd3d7";
const OURS_TRUNK = "#f0b429";
const OURS_LOCAL = "#c08f1f";
const OURS_ANT = "#ffe9a8";
/** Больше половины трассы собрано нами — красим её как нашу. */
const OURS_SHARE = 0.5;

/** Зум, с которого проявляются локальные трассы и вторые подписи. */
const DETAIL_ZOOM = 6.4;
/** Зум, с которого подписываются сёла. */
const CLOSE_ZOOM = 8;

function plural(n: number, one: string, few: string, many: string): string {
  const m100 = Math.abs(n) % 100;
  const m10 = Math.abs(n) % 10;
  if (m100 >= 11 && m100 <= 14) return many;
  if (m10 === 1) return one;
  if (m10 >= 2 && m10 <= 4) return few;
  return many;
}

/**
 * Подсказка трассы — предложениями, а не строкой из цифр через точку.
 *
 * «до 51 повтора на плече · встречное движение: 5» владелец прочитать не
 * смог, и он прав: это внутренняя терминология. Человеку надо знать,
 * откуда куда летят, сколько раз это видели и насколько данным верить.
 */
function chainTip(chain: Chain): string {
  const lines = [`<b>${chain.from} → ${chain.to}</b>`];
  if (chain.via.length) {
    lines.push(`Путь: ${chain.via.join(" → ")} и дальше.`);
  }
  const total = chain.nm + chain.cp;
  lines.push(
    `Этот путь повторился ${total} ${plural(total, "раз", "раза", "раз")}.`
  );
  if (chain.nm && chain.cp) {
    lines.push(
      `Из них ${chain.nm} ${plural(chain.nm, "раз", "раза", "раз")} путь ` +
        `описал сам источник, ${chain.cp} — наша реконструкция по волне ` +
        `фиксаций.`
    );
  } else if (chain.cp) {
    lines.push(
      "Источники этот путь не описывали — он восстановлен по тому, как " +
        "волна фиксаций шла по карте."
    );
  }
  if (chain.r) {
    lines.push(`Иногда летят и в обратную сторону (${chain.r} раз).`);
  }
  if (chain.kor) lines.push("<i>Нажмите — покажу карточку коридора.</i>");
  return lines.join("<br>");
}

function init(): void {
  const target = document.getElementById("routes-map");
  if (!target) return;

  // Метку версии проставляет генератор страницы: имя файла фиксировано, а
  // Apache отдаёт его с недельным кэшем — без метки браузер неделю
  // показывал бы вчерашние коридоры.
  const version = target.dataset.version;
  fetch(`/data/corridors.json${version ? `?v=${version}` : ""}`)
    .then((response) => response.json())
    .then((graph: Graph) => render(target, graph))
    .catch(() => {
      target.innerHTML =
        '<p style="padding:20px;color:#aab4ad">Не удалось загрузить данные коридоров.</p>';
    });
}

function render(target: HTMLElement, graph: Graph): void {
  const { chains, labels } = graph;

  // --- Трассы ----------------------------------------------------------
  const chainSource = new VectorSource();
  for (const chain of chains) {
    const coords = chain.pts.map(([lat, lon]) => fromLonLat([lon, lat]));
    const feature = new Feature(new LineString(coords));
    feature.set("chain", chain);
    chainSource.addFeature(feature);
  }

  // Бегущие штрихи: смещение фазы обновляется таймером, направление
  // полёта видно без наконечников. prefers-reduced-motion отключает.
  let dashOffset = 0;
  const animate = !window.matchMedia("(prefers-reduced-motion: reduce)")
    .matches;

  const chainStyle = (feature: FeatureLike, resolution: number): Style[] => {
    const chain = feature.get("chain") as Chain;
    const zoom = map.getView().getZoomForResolution(resolution) ?? 5;
    if (!chain.t && zoom < DETAIL_ZOOM) return [];
    const trunk = Boolean(chain.t);
    const ours = chain.cs >= OURS_SHARE;
    const width = trunk ? 2 + 3.6 * chain.s : 1.1 + 1.6 * chain.s;
    return [
      new Style({
        stroke: new Stroke({
          color: ours ? (trunk ? OURS_TRUNK : OURS_LOCAL)
                      : (trunk ? TRUNK : LOCAL),
          width,
          lineCap: "round",
          lineJoin: "round"
        })
      }),
      new Style({
        stroke: new Stroke({
          color: ours ? OURS_ANT : ANT,
          width: Math.max(1, width * 0.45),
          lineDash: [2, 16],
          lineDashOffset: dashOffset,
          lineCap: "round"
        })
      })
    ];
  };

  const chainLayer = new VectorLayer({
    source: chainSource,
    style: chainStyle,
    zIndex: 10
  });

  // --- Подписи путевых точек: имена без кружков --------------------------
  const labelSource = new VectorSource();
  for (const label of labels) {
    const feature = new Feature(
      new Point(fromLonLat([label.lon, label.lat]))
    );
    feature.set("label", label);
    labelSource.addFeature(feature);
  }

  const labelStyle = (feature: FeatureLike, resolution: number): Style | undefined => {
    const label = feature.get("label") as MapLabel;
    const zoom = map.getView().getZoomForResolution(resolution) ?? 5;
    if (label.t === 2 && zoom < DETAIL_ZOOM) return undefined;
    if (label.t === 3 && zoom < CLOSE_ZOOM) return undefined;
    return new Style({
      text: new Text({
        text: label.name,
        font: "11px Inter, system-ui, sans-serif",
        fill: new Fill({ color: "#dfe6df" }),
        stroke: new Stroke({ color: "rgba(10,14,13,0.9)", width: 3 })
      })
    });
  };

  const labelLayer = new VectorLayer({
    source: labelSource,
    style: labelStyle,
    declutter: true,
    zIndex: 20
  });

  // --- Карта -----------------------------------------------------------
  const map = new OlMap({
    target,
    controls: defaultControls({ attribution: true, rotate: false }),
    layers: [
      new TileLayer({
        source: new XYZ({
          url: BASEMAP_URL,
          attributions: ATTRIBUTION,
          crossOrigin: "anonymous",
          maxZoom: 20
        })
      }),
      chainLayer,
      labelLayer
    ],
    view: new View({
      center: fromLonLat([37.8, 49.4]),
      zoom: 5.7,
      minZoom: 4.4,
      maxZoom: 11,
      extent: transformExtent([22, 40, 62, 62], "EPSG:4326", "EPSG:3857")
    })
  });

  // Контейнер может получить размер позже инициализации (вкладки,
  // свёрнутые панели): OL сам за этим не следит.
  new ResizeObserver(() => map.updateSize()).observe(target);

  if (animate) {
    // 30 кадров в секунду достаточно: перерисовка векторного слоя каждые
    // 33 мс — заметно дешевле requestAnimationFrame на слабых телефонах.
    window.setInterval(() => {
      dashOffset = (dashOffset - 1 + 18) % 18;
      chainLayer.changed();
    }, 66);
  }

  // --- Подсказка и переход к карточке ----------------------------------
  const tip = document.createElement("div");
  tip.style.cssText =
    "position:absolute;pointer-events:none;background:rgba(12,16,15,.95);" +
    "border:1px solid #28322c;border-radius:10px;padding:10px 13px;" +
    "font-size:13px;line-height:1.5;color:#c9d2cb;max-width:330px;" +
    "display:none;z-index:5;box-shadow:0 6px 24px rgba(0,0,0,.45);";
  target.appendChild(tip);

  map.on("pointermove", (event) => {
    const feature = map.forEachFeatureAtPixel(event.pixel, (found) => found, {
      hitTolerance: 6,
      layerFilter: (layer) => layer === chainLayer
    });
    if (!feature) {
      tip.style.display = "none";
      target.style.cursor = "";
      return;
    }
    const chain = feature.get("chain") as Chain;
    // Разметка тут своя, собранная из данных генератора: имена мест уже
    // прошли справочник, произвольного текста в подсказке нет.
    tip.innerHTML = chainTip(chain);
    tip.style.display = "block";
    tip.style.left = `${event.pixel[0] + 14}px`;
    tip.style.top = `${event.pixel[1] + 12}px`;
    target.style.cursor = chain.kor ? "pointer" : "default";
  });

  map.on("click", (event) => {
    const feature = map.forEachFeatureAtPixel(event.pixel, (found) => found, {
      hitTolerance: 6,
      layerFilter: (layer) => layer === chainLayer
    });
    const anchor = feature?.get("chain")?.kor as string | undefined;
    if (anchor) {
      window.location.hash = anchor;
    }
  });
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
