/**
 * Карта коридоров на странице /marshruty/.
 *
 * Тот же движок и та же тайловая подложка, что у живой карты: границы и
 * берега рисует OpenLayers, а не самодельная SVG-проекция — три подхода
 * к рукодельной картографии показали, что это тупик.
 *
 * Данные — dist/data/corridors.json, его ежечасно пересобирает
 * scripts/routes_page.py. Здесь только отрисовка: рёбра с толщиной по
 * весу, бегущие штрихи по направлению, узлы с подписями (declutter),
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
import { Circle as CircleStyle, Fill, Stroke, Style, Text } from "ol/style";
import type { FeatureLike } from "ol/Feature";
import { defaults as defaultControls } from "ol/control";

const BASEMAP_URL =
  "https://{a-d}.basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}.png";
const ATTRIBUTION = "© OpenStreetMap contributors, © CARTO";

type GraphNode = {
  i: number; lat: number; lon: number;
  name: string; w: number; s: number; t: number;
};
type GraphEdge = {
  a: number; b: number; n: number; f: number; r: number;
  nm: number; cp: number; s: number; t: number;
  arc?: [number, number][]; kor?: string;
};
type Graph = { nodes: GraphNode[]; edges: GraphEdge[] };

const TRUNK = "#f0475a";
const LOCAL = "#c33b49";
const ANT = "#ffd3d7";
const NODE_FILL = "#ffb3ba";

/** Зум, с которого проявляются локальные ветки и вторые подписи. */
const DETAIL_ZOOM = 6.6;

function plural(n: number, one: string, few: string, many: string): string {
  const m100 = Math.abs(n) % 100;
  const m10 = Math.abs(n) % 10;
  if (m100 >= 11 && m100 <= 14) return many;
  if (m10 === 1) return one;
  if (m10 >= 2 && m10 <= 4) return few;
  return many;
}

function edgeTip(edge: GraphEdge, nodes: GraphNode[]): string {
  const parts = [
    `${nodes[edge.a].name} → ${nodes[edge.b].name}`,
    `${edge.n} ${plural(edge.n, "повтор", "повтора", "повторов")}`
  ];
  if (edge.r) parts.push(`туда ${edge.f}, обратно ${edge.r}`);
  if (edge.cp) parts.push(`восстановлено по фиксациям: ${edge.cp}`);
  return parts.join(" · ");
}

function init(): void {
  const target = document.getElementById("routes-map");
  if (!target) return;

  fetch("/data/corridors.json")
    .then((response) => response.json())
    .then((graph: Graph) => render(target, graph))
    .catch(() => {
      target.innerHTML =
        '<p style="padding:20px;color:#aab4ad">Не удалось загрузить данные коридоров.</p>';
    });
}

function render(target: HTMLElement, graph: Graph): void {
  const { nodes, edges } = graph;
  const byIndex = new Map(nodes.map((node) => [node.i, node]));
  const nodeAt = (index: number) => byIndex.get(index) as GraphNode;

  // --- Рёбра -----------------------------------------------------------
  const edgeSource = new VectorSource();
  for (const edge of edges) {
    const a = nodeAt(edge.a);
    const b = nodeAt(edge.b);
    const coords = (edge.arc ?? [[a.lat, a.lon], [b.lat, b.lon]]).map(
      ([lat, lon]) => fromLonLat([lon, lat])
    );
    const feature = new Feature(new LineString(coords));
    feature.set("edge", edge);
    edgeSource.addFeature(feature);
  }

  // Бегущие штрихи: смещение фазы обновляется таймером, направление
  // полёта видно без наконечников. prefers-reduced-motion отключает.
  let dashOffset = 0;
  const animate = !window.matchMedia("(prefers-reduced-motion: reduce)")
    .matches;

  const edgeStyle = (feature: FeatureLike, resolution: number): Style[] => {
    const edge = feature.get("edge") as GraphEdge;
    const view = map.getView();
    const zoom = view.getZoomForResolution(resolution) ?? 5;
    if (!edge.t && zoom < DETAIL_ZOOM) return [];
    const trunk = Boolean(edge.t);
    const width = trunk ? 1.8 + 3.4 * edge.s : 1 + 1.4 * edge.s;
    const styles = [
      new Style({
        stroke: new Stroke({
          color: trunk ? TRUNK : LOCAL,
          width,
          lineCap: "round"
        })
      }),
      new Style({
        stroke: new Stroke({
          color: ANT,
          width: Math.max(1, width * 0.5),
          lineDash: [2, 14],
          lineDashOffset: dashOffset,
          lineCap: "round"
        })
      })
    ];
    styles[0].getStroke()!.setLineJoin("round");
    return styles;
  };

  const edgeLayer = new VectorLayer({
    source: edgeSource,
    style: edgeStyle,
    zIndex: 10
  });

  // --- Узлы ------------------------------------------------------------
  const nodeSource = new VectorSource();
  for (const node of nodes) {
    const feature = new Feature(new Point(fromLonLat([node.lon, node.lat])));
    feature.set("node", node);
    nodeSource.addFeature(feature);
  }

  const nodeStyle = (feature: FeatureLike, resolution: number): Style | undefined => {
    const node = feature.get("node") as GraphNode;
    const zoom = map.getView().getZoomForResolution(resolution) ?? 5;
    const detailed = zoom >= DETAIL_ZOOM;
    if (node.t >= 4 && !detailed) return undefined;
    const radius = 2.5 + 3.5 * node.s;
    const labeled = node.t === 1 || (detailed && node.t <= 3);
    return new Style({
      image: new CircleStyle({
        radius,
        fill: new Fill({ color: NODE_FILL }),
        stroke: new Stroke({ color: "#0a0e0d", width: 1 })
      }),
      text: labeled
        ? new Text({
            text: node.name,
            offsetY: -radius - 7,
            font: "11px Inter, system-ui, sans-serif",
            fill: new Fill({ color: "#e6ebe6" }),
            stroke: new Stroke({ color: "rgba(10,14,13,0.85)", width: 3 })
          })
        : undefined
    });
  };

  const nodeLayer = new VectorLayer({
    source: nodeSource,
    style: nodeStyle,
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
      edgeLayer,
      nodeLayer
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
      dashOffset = (dashOffset - 1 + 16) % 16;
      edgeLayer.changed();
    }, 66);
  }

  // --- Подсказка и переход к карточке ----------------------------------
  const tip = document.createElement("div");
  tip.style.cssText =
    "position:absolute;pointer-events:none;background:rgba(12,16,15,.94);" +
    "border:1px solid #28322c;border-radius:8px;padding:7px 11px;" +
    "font-size:13px;color:#dfe6df;max-width:300px;display:none;z-index:5;";
  target.appendChild(tip);

  map.on("pointermove", (event) => {
    const feature = map.forEachFeatureAtPixel(event.pixel, (found) => found, {
      hitTolerance: 6
    });
    if (!feature) {
      tip.style.display = "none";
      target.style.cursor = "";
      return;
    }
    const edge = feature.get("edge") as GraphEdge | undefined;
    const node = feature.get("node") as GraphNode | undefined;
    tip.textContent = edge
      ? edgeTip(edge, nodes)
      : node
        ? `${node.name} · узел, ${node.w} ${plural(node.w, "повтор", "повтора", "повторов")}`
        : "";
    if (!tip.textContent) {
      tip.style.display = "none";
      return;
    }
    tip.style.display = "block";
    tip.style.left = `${event.pixel[0] + 14}px`;
    tip.style.top = `${event.pixel[1] + 12}px`;
    target.style.cursor = edge?.kor ? "pointer" : "default";
  });

  map.on("click", (event) => {
    const feature = map.forEachFeatureAtPixel(event.pixel, (found) => found, {
      hitTolerance: 6
    });
    const anchor = feature?.get("edge")?.kor as string | undefined;
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
