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
const ANT = "#ffd3d7";
const OURS_TRUNK = "#f0b429";
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
/** Тот же цвет, но полупрозрачный: приглушение невыбранных трасс. */
function withAlpha(hex: string, alpha: number): string {
  const value = parseInt(hex.slice(1), 16);
  return `rgba(${(value >> 16) & 255},${(value >> 8) & 255},${value & 255},${alpha})`;
}

function chainKm(chain: Chain): number {
  let total = 0;
  for (let i = 1; i < chain.pts.length; i += 1) {
    const [lat0, lon0] = chain.pts[i - 1];
    const [lat1, lon1] = chain.pts[i];
    const dx = (lon1 - lon0) * 111 * Math.cos(((lat0 + lat1) / 2) * Math.PI / 180);
    const dy = (lat1 - lat0) * 111;
    total += Math.hypot(dx, dy);
  }
  return Math.round(total);
}

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

  let selected: Chain | null = null;

  const chainStyle = (feature: FeatureLike): Style[] => {
    const chain = feature.get("chain") as Chain;
    const chosen = selected === chain;
    // Видно всё и сразу. Прежде на обзоре показывались только шестьдесят
    // самых тяжёлых трасс, и весь север выглядел пустым: из 575 трасс
    // севернее Воронежа в эту шестидесятку попадали три. Иерархия теперь
    // не в прятках, а в толщине и яркости.
    const trunk = Boolean(chain.t);
    const ours = chain.cs >= OURS_SHARE;
    const base = 0.7 + 3.6 * Math.pow(chain.s, 1.6);
    const width = chosen ? base + 3 : base;
    // Яркость — вес трассы: редкий путь виден бледной нитью, частый
    // горит. Пока одна выбрана, остальные приглушены вдвойне.
    const alpha = chosen ? 1 : (0.22 + 0.68 * chain.s)
      * (selected !== null ? 0.3 : 1);
    const paint = (color: string) => withAlpha(color, Math.min(1, alpha));
    const styles = [
      new Style({
        stroke: new Stroke({
          color: paint(ours ? OURS_TRUNK : TRUNK),
          width,
          lineCap: "round",
          lineJoin: "round"
        }),
        zIndex: chosen ? 30 : Math.round(chain.s * 10)
      })
    ];
    // Бегущие штрихи — только на заметных трассах: на девятистах нитях
    // они превращаются в рябь.
    if (chosen || chain.s > 0.5) {
      styles.push(new Style({
        stroke: new Stroke({
          color: paint(ours ? OURS_ANT : ANT),
          width: Math.max(0.8, width * 0.42),
          lineDash: [2, 16],
          lineDashOffset: dashOffset,
          lineCap: "round"
        }),
        zIndex: chosen ? 31 : 12
      }));
    }
    if (chosen) {
      // Свечение под выбранной линией — чтобы её было видно в клубке.
      styles.unshift(new Style({
        stroke: new Stroke({
          color: "rgba(255,255,255,0.28)",
          width: width + 8,
          lineCap: "round",
          lineJoin: "round"
        }),
        zIndex: 2
      }));
    }
    return styles;
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
      center: fromLonLat([38, 50]),
      zoom: 5.4,
      minZoom: 4.2,
      maxZoom: 11,
      extent: transformExtent([22, 40, 62, 62], "EPSG:4326", "EPSG:3857")
    })
  });

  // Кадр подбирается по самим данным: жёстко заданный центр однажды уже
  // оставил весь север за краем экрана. Небольшой отступ — чтобы линии не
  // упирались в рамку.
  const extent = chainSource.getExtent();
  if (extent && Number.isFinite(extent[0])) {
    map.getView().fit(extent, { padding: [28, 28, 28, 28], maxZoom: 6.4 });
  }

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

  // --- Карточка выбранной трассы ---------------------------------------
  const card = document.createElement("div");
  card.className = "chain-card";
  target.appendChild(card);

  function describe(chain: Chain): string {
    const total = chain.nm + chain.cp;
    const rows: string[] = [];
    rows.push(`<dt>Длина</dt><dd>${chainKm(chain)} км</dd>`);
    rows.push(
      `<dt>Повторов</dt><dd>${total} ${plural(total, "раз", "раза", "раз")}</dd>`
    );
    if (chain.nm) {
      rows.push(`<dt>Описан источником</dt><dd>${chain.nm}</dd>`);
    }
    if (chain.cp) {
      rows.push(`<dt>Восстановлено нами</dt><dd>${chain.cp}</dd>`);
    }
    if (chain.r) {
      rows.push(`<dt>Обратно</dt><dd>${chain.r}</dd>`);
    }
    const path = chain.via.length
      ? `<p class="path">${[chain.from, ...chain.via, chain.to].join(" → ")}</p>`
      : "";
    const link = chain.kor
      ? `<p style="margin:10px 0 0"><a href="#${chain.kor}">Карточка коридора ниже →</a></p>`
      : "";
    // Оговорка стоит в каждой карточке намеренно: именно здесь человек
    // видит конкретный путь и легче всего принимает линию за траекторию.
    const caveat =
      '<p style="margin:10px 0 0;color:#7d8a83;font-size:12px">' +
      "Направление, а не точный маршрут: между названными точками борт " +
      "идёт где угодно.</p>";
    return (
      `<button class="close" type="button" aria-label="Закрыть">×</button>` +
      `<h3>${chain.from} → ${chain.to}</h3>${path}` +
      `<dl>${rows.join("")}</dl>${link}${caveat}`
    );
  }

  function select(chain: Chain | null): void {
    selected = chain;
    if (chain) {
      card.innerHTML = describe(chain);
      card.classList.add("is-open");
    } else {
      card.classList.remove("is-open");
    }
    chainLayer.changed();
  }

  card.addEventListener("click", (event) => {
    if ((event.target as HTMLElement).classList.contains("close")) {
      select(null);
    }
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") select(null);
  });

  map.on("click", (event) => {
    const feature = map.forEachFeatureAtPixel(event.pixel, (found) => found, {
      hitTolerance: 6,
      layerFilter: (layer) => layer === chainLayer
    });
    select((feature?.get("chain") as Chain | undefined) ?? null);
  });
}

/**
 * Поиск по галерее коридоров.
 *
 * Ищет по именам концов и промежуточных точек и по субъектам, которым они
 * принадлежат: строку собирает генератор в data-q каждой карточки, поэтому
 * «Крым» находит Джанкой, а «Кубань» — Новороссийск.
 */
function initFinder(): void {
  const input = document.getElementById("finder") as HTMLInputElement | null;
  const counter = document.getElementById("finder-count");
  const more = document.getElementById("finder-more");
  if (!input) return;
  // Каталог — строки списка: там все коридоры. Витрина с мини-картами
  // показывает первую дюжину и на время поиска уходит, чтобы результат
  // начинался с самого точного совпадения, а не с картинок.
  const rows = Array.from(document.querySelectorAll<HTMLElement>(".corridor"));
  const gallery = document.getElementById("gallery");
  const list = rows[0]?.parentElement ?? null;
  /** Сколько коридоров видно без поиска: остальные ждут кнопки. */
  const VISIBLE = 60;
  let expanded = false;

  const apply = () => {
    const query = input.value.trim().toLowerCase();
    let shown = 0;
    for (const row of rows) {
      const hit = !query || (row.dataset.q ?? "").includes(query);
      // Без запроса показываем первые шестьдесят; поиск идёт по всем.
      row.hidden = !hit || (!query && !expanded && shown >= VISIBLE);
      if (hit) shown += 1;
    }
    if (gallery) gallery.hidden = Boolean(query);
    if (counter) {
      counter.textContent = query ? `${shown} из ${rows.length}` : "";
    }
    if (more) {
      more.hidden = Boolean(query) || expanded || rows.length <= VISIBLE;
    }
    // Совпадение по названию коридора важнее совпадения по региону: на
    // запрос «Краснодар» сначала идут пути в Краснодар, а потом всё
    // остальное в Краснодарском крае.
    if (query && list) {
      const exact = rows.filter(
        (row) => !row.hidden && (row.dataset.name ?? "").includes(query)
      );
      for (let index = exact.length - 1; index >= 0; index -= 1) {
        list.prepend(exact[index]);
      }
    }
  };

  input.addEventListener("input", apply);
  more?.addEventListener("click", () => {
    expanded = true;
    apply();
  });

  // Клик по трассе ведёт к строке каталога, а она может лежать за
  // шестидесятой — тогда каталог раскрывается сам, иначе переход
  // упирался в пустоту.
  const reveal = () => {
    const anchor = window.location.hash.slice(1);
    if (!anchor) return;
    const target = document.getElementById(anchor);
    if (!target || !target.hidden) return;
    expanded = true;
    input.value = "";
    apply();
    target.scrollIntoView({ block: "center" });
  };
  window.addEventListener("hashchange", reveal);

  apply();
  reveal();
}

function boot(): void {
  init();
  initFinder();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", boot);
} else {
  boot();
}
