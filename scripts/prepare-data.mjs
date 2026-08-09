import {
  existsSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  rmSync,
  statSync,
  writeFileSync
} from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { execFileSync } from "node:child_process";
import { readShapefileFromZip } from "./shapefile.mjs";

const root = dirname(fileURLToPath(new URL("../package.json", import.meta.url)));
const outData = join(root, "public", "data");

// --- Пропуск пересборки ------------------------------------------------------
//
// Скрипт висит на каждом `npm run dev` и жевал исходники по минуте, хотя
// они не менялись месяцами. Хуже того: пересборка затирала в districts.json
// имена и родителей, дописанные pipeline.gazetteer, — до следующего запуска
// справочника карта жила с ободранными полигонами.
//
// Штамп считается ТОЛЬКО по входам (исходные данные и сами скрипты), а не по
// выходам: выходные файлы правит gazetteer, и его правки не повод для
// пересборки. Принудительно: npm run prepare:data -- --force
const stampPath = join(outData, ".prepare-stamp.json");
const expectedOutputs = [
  "regions.json", "districts.json", "places.json", "summary.json",
  "water-bodies.json", "rivers.json", "river-network-major.json",
  "river-network-detail.json", "urban-areas.json", "roads.json",
  "railways.json", "terrain-regions.json", "glaciers.json", "land-cover.json"
];

const latestInputMtime = () => {
  let latest = 0;
  const visit = (path) => {
    let info;
    try {
      info = statSync(path);
    } catch {
      return;
    }
    if (info.isDirectory()) {
      for (const entry of readdirSync(path)) visit(join(path, entry));
      return;
    }
    latest = Math.max(latest, info.mtimeMs);
  };
  visit(join(root, "research", "data_sources"));
  visit(join(root, "research", "radarmap_reference", "data"));
  visit(join(root, "scripts", "prepare-data.mjs"));
  visit(join(root, "scripts", "shapefile.mjs"));
  return Math.round(latest);
};

const inputsStamp = latestInputMtime();
const forced = process.argv.includes("--force");
if (!forced && existsSync(stampPath)) {
  try {
    const stored = JSON.parse(readFileSync(stampPath, "utf8"));
    const complete = expectedOutputs.every((name) => existsSync(join(outData, name)));
    if (stored.inputs === inputsStamp && complete) {
      console.log("Исходные данные не менялись — пересборка пропущена (--force для принудительной).");
      process.exit(0);
    }
  } catch {
    // Битый штамп — просто пересобираем.
  }
}

mkdirSync(outData, { recursive: true });
rmSync(join(outData, "cities.json"), { force: true });
rmSync(join(root, "public", "icons"), { force: true, recursive: true });

const readJson = (path) => JSON.parse(readFileSync(join(root, path), "utf8"));

// Полигон знает свою зону в справочнике: по полям zone и region карта
// красит регион при тревоге в его районе и открывает карточку по клику.
// Эти поля дописывает pipeline.gazetteer — здесь их неоткуда взять, и
// перезапись файла их роняла. Штамп от этого не спасает: любая правка
// самого скрипта его протухает, а следующая сборка выкладывает на карту
// полигоны, ни к чему не привязанные.
const KEPT_FACTS = ["zone", "region"];

const keepFacts = (name, collection) => {
  const path = join(outData, name);
  if (!existsSync(path) || !Array.isArray(collection?.features)) return collection;
  let known;
  try {
    known = new Map(JSON.parse(readFileSync(path, "utf8")).features
      .map((feature) => [feature.properties?.id, feature.properties]));
  } catch {
    return collection;   // Битый прошлый файл — не повод терять новый.
  }
  for (const feature of collection.features) {
    const previous = known.get(feature.properties?.id);
    if (!previous) continue;
    for (const field of KEPT_FACTS) {
      if (previous[field] != null && feature.properties[field] == null) {
        feature.properties[field] = previous[field];
      }
    }
  }
  return collection;
};

const writeJson = (name, data) => {
  writeFileSync(join(outData, name), JSON.stringify(keepFacts(name, data)));
};

const roundNumber = (value, precision) => {
  if (typeof value !== "number") return value;
  const factor = 10 ** precision;
  return Math.round(value * factor) / factor;
};

const finiteNumber = (value, fallback = null) => {
  const numberValue = Number(value);
  return Number.isFinite(numberValue) ? numberValue : fallback;
};

const roundCoords = (value, precision) => {
  if (typeof value === "number") return roundNumber(value, precision);
  if (Array.isArray(value)) return value.map((item) => roundCoords(item, precision));
  return value;
};

const compactFeatureCollection = (collection, mapProperties, precision) => ({
  type: "FeatureCollection",
  features: collection.features.map((feature, index) => ({
    type: "Feature",
    id: feature.id ?? feature.properties?.id ?? feature.properties?.shapeID ?? index,
    properties: mapProperties(feature.properties ?? {}, index),
    geometry: {
      type: feature.geometry.type,
      coordinates: roundCoords(feature.geometry.coordinates, precision)
    }
  }))
});

const getCoordinatePoints = (coordinates, points = []) => {
  if (typeof coordinates?.[0] === "number") {
    points.push(coordinates);
    return points;
  }

  for (const item of coordinates ?? []) getCoordinatePoints(item, points);
  return points;
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

const createRegionPointMatcher = (regionCollection) => {
  const polygons = regionCollection.features.flatMap((feature) =>
    geometryPolygons(feature.geometry).flatMap((polygon) => {
      const exterior = polygon[0];
      if (!exterior) return [];
      return [{ exterior, holes: polygon.slice(1), bounds: ringBounds(exterior) }];
    })
  );

  return ([lon, lat]) =>
    polygons.some((polygon) => {
      const bounds = polygon.bounds;
      if (lon < bounds[0] - 0.2 || lon > bounds[2] + 0.2 || lat < bounds[1] - 0.2 || lat > bounds[3] + 0.2) {
        return false;
      }
      return pointInRing([lon, lat], polygon.exterior) && !polygon.holes.some((hole) => pointInRing([lon, lat], hole));
    });
};

const featureTouchesRegions = (feature, matchesRegionPoint) =>
  getCoordinatePoints(feature.geometry?.coordinates).some((point) => matchesRegionPoint(point));

const geometryContainsPoint = (geometry, point) =>
  geometryPolygons(geometry).some((polygon) => {
    const exterior = polygon[0];
    if (!exterior) return false;
    return pointInRing(point, exterior) && !polygon.slice(1).some((hole) => pointInRing(point, hole));
  });

const geometryBounds = (geometry) => {
  const bounds = [Infinity, Infinity, -Infinity, -Infinity];
  for (const [lon, lat] of getCoordinatePoints(geometry?.coordinates)) {
    bounds[0] = Math.min(bounds[0], lon);
    bounds[1] = Math.min(bounds[1], lat);
    bounds[2] = Math.max(bounds[2], lon);
    bounds[3] = Math.max(bounds[3], lat);
  }
  return bounds;
};

const boundsTouch = (left, right, padding = 0) =>
  !(left[2] < right[0] - padding || left[0] > right[2] + padding || left[3] < right[1] - padding || left[1] > right[3] + padding);

const hasCyrillic = (value) => /[А-Яа-яЁё]/.test(value);

const ruToLatin = {
  а: "a",
  б: "b",
  в: "v",
  г: "g",
  д: "d",
  е: "e",
  ё: "e",
  ж: "zh",
  з: "z",
  и: "i",
  й: "y",
  к: "k",
  л: "l",
  м: "m",
  н: "n",
  о: "o",
  п: "p",
  р: "r",
  с: "s",
  т: "t",
  у: "u",
  ф: "f",
  х: "h",
  ц: "ts",
  ч: "ch",
  ш: "sh",
  щ: "sch",
  ъ: "",
  ы: "y",
  ь: "",
  э: "e",
  ю: "yu",
  я: "ya"
};

const normalizeLatin = (value) =>
  value
    .toLowerCase()
    .replace(/[а-яё]/g, (char) => ruToLatin[char] ?? char)
    .replace(/[^a-z0-9]+/g, "");

const levenshtein = (left, right) => {
  if (left === right) return 0;
  if (!left) return right.length;
  if (!right) return left.length;

  const previous = Array.from({ length: right.length + 1 }, (_, index) => index);
  const current = new Array(right.length + 1);

  for (let i = 1; i <= left.length; i += 1) {
    current[0] = i;
    for (let j = 1; j <= right.length; j += 1) {
      const cost = left[i - 1] === right[j - 1] ? 0 : 1;
      current[j] = Math.min(current[j - 1] + 1, previous[j] + 1, previous[j - 1] + cost);
    }
    previous.splice(0, previous.length, ...current);
  }

  return previous[right.length];
};

const pickPlaceName = (name, asciiName, alternates) => {
  if (hasCyrillic(name)) return name;
  const cyrillic = alternates
    .split(",")
    .map((item) => item.trim())
    .filter((item) => item.length > 1 && item.length <= 80 && /^[А-Яа-яЁё0-9 .'-]+$/.test(item));

  if (cyrillic.length > 0) {
    const target = normalizeLatin(asciiName || name);
    return cyrillic
      .map((item, index) => ({
        item,
        index,
        score: target ? levenshtein(normalizeLatin(item), target) : index
      }))
      .sort((a, b) => a.score - b.score || a.index - b.index)[0].item;
  }

  return name || asciiName;
};

const hasCyrillicAny = (value) => /[А-Яа-яЁёІіЇїЄєҐґ]/.test(value);
const hasUkrainianLetters = (value) => /[ІіЇїЄєҐґ]/.test(value);
const russianPlaceCandidatePattern = /^[А-Яа-яЁё0-9 .'\-()]+$/;

const supplementalPlaceNameOverrides = new Map([
  ["Запоріжжя", "Запорожье"],
  ["Запорізьке", "Запорожское"],
  ["Сєвєродонецьк", "Северодонецк"],
  ["Сіверськодонецьк", "Северодонецк"],
  ["Донецьк", "Донецк"],
  ["Донецьке", "Донецкое"],
  ["Луганськ", "Луганск"],
  ["Луганське", "Луганское"],
  ["Маріуполь", "Мариуполь"],
  ["Мелітополь", "Мелитополь"],
  ["Бердянськ", "Бердянск"],
  ["Авдіївка", "Авдеевка"],
  ["Горлівка", "Горловка"],
  ["Єнакієве", "Енакиево"],
  ["Кадіївка", "Кадиевка"],
  ["Іловайськ", "Иловайск"],
  ["Дебальцеве", "Дебальцево"],
  ["Сніжне", "Снежное"],
  ["Амвросіївка", "Амвросиевка"],
  ["Вугледар", "Угледар"],
  ["Волноваха", "Волноваха"],
  ["Олешки", "Алешки"],
  ["Нова Каховка", "Новая Каховка"]
]);

const normalizeSupplementalRussianPlaceName = (value) => {
  const source = removeTextMarks(String(value || "").replace(/[’`]/g, "ь").replace(/\s+/g, " ").trim());
  if (!source) return source;

  const exact = supplementalPlaceNameOverrides.get(source);
  if (exact) return exact;

  return source
    .replace(/Запоріжжя/g, "Запорожье")
    .replace(/запоріжжя/g, "запорожье")
    .replace(/Запорижжя/g, "Запорожье")
    .replace(/запорижжя/g, "запорожье")
    .replace(/(^|[\s-])Нове(?=$|[\s-])/g, "$1Новое")
    .replace(/(^|[\s-])нове(?=$|[\s-])/g, "$1новое")
    .replace(/(^|[\s-])Нова(?=$|[\s-])/g, "$1Новая")
    .replace(/(^|[\s-])нова(?=$|[\s-])/g, "$1новая")
    .replace(/(^|[\s-])Новий(?=$|[\s-])/g, "$1Новый")
    .replace(/(^|[\s-])новий(?=$|[\s-])/g, "$1новый")
    .replace(/(^|[\s-])Старе(?=$|[\s-])/g, "$1Старое")
    .replace(/(^|[\s-])старе(?=$|[\s-])/g, "$1старое")
    .replace(/(^|[\s-])Стара(?=$|[\s-])/g, "$1Старая")
    .replace(/(^|[\s-])стара(?=$|[\s-])/g, "$1старая")
    .replace(/(^|[\s-])Старий(?=$|[\s-])/g, "$1Старый")
    .replace(/(^|[\s-])старий(?=$|[\s-])/g, "$1старый")
    .replace(/(^|[\s-])Мисто(?=$|[\s-])/g, "$1Место")
    .replace(/(^|[\s-])мисто(?=$|[\s-])/g, "$1место")
    .replace(/іївка$/i, (match) => (match[0] === "І" ? "Еевка" : "еевка"))
    .replace(/ївка$/i, (match) => (match[0] === "Ї" ? "Евка" : "евка"))
    .replace(/івка$/i, (match) => (match[0] === "І" ? "Овка" : "овка"))
    .replace(/І/g, "И")
    .replace(/і/g, "и")
    .replace(/Ї/g, "И")
    .replace(/ї/g, "и")
    .replace(/Є/g, "Е")
    .replace(/є/g, "е")
    .replace(/Ґ/g, "Г")
    .replace(/ґ/g, "г")
    .replace(/([Сс])ьк/g, "$1к")
    .replace(/([Цц])ьк/g, "$1к")
    .replace(/([Зз])ьк/g, "$1к")
    .replace(/([Сс])ьке/g, "$1кое")
    .replace(/([Цц])ьке/g, "$1кое")
    .replace(/([Зз])ьке/g, "$1кое")
    .replace(/([Сс])ька/g, "$1кая")
    .replace(/([Цц])ька/g, "$1кая")
    .replace(/([Зз])ька/g, "$1кая")
    .replace(/([Сс])ький/g, "$1кий")
    .replace(/([Цц])ький/g, "$1кий")
    .replace(/([Зз])ький/g, "$1кий")
    .replace(/([Цц])ь/g, "$1")
    .replace(/жжя$/i, (match) => (match[0] === "Ж" ? "жье" : "жье"));
};

const isLikelySupplementalPlaceName = (value) => {
  const normalized = normalizeSupplementalRussianPlaceName(value);
  return (
    normalized.length > 1 &&
    normalized.length <= 80 &&
    hasCyrillicAny(normalized) &&
    russianPlaceCandidatePattern.test(normalized) &&
    !normalized.includes("ош") &&
    !normalized.toLowerCase().includes("місто")
  );
};

const pickSupplementalRussianPlaceName = (name, asciiName, alternates) => {
  const candidates = [name, ...splitAlternates(alternates)]
    .map((item) => item.trim())
    .filter(isLikelySupplementalPlaceName)
    .map((item) => normalizeSupplementalRussianPlaceName(item));

  if (candidates.length > 0) {
    const target = normalizeLatin(asciiName || name);
    return unique(candidates)
      .map((item, index) => {
        const value = item.toLowerCase();
        const penalty =
          value.includes("город ") || value.includes("столица") || value.includes("совет") || hasUkrainianLetters(item)
            ? 20
            : 0;
        return {
          item,
          index,
          score: (target ? levenshtein(normalizeLatin(item), target) : index) + penalty
        };
      })
      .sort((a, b) => a.score - b.score || a.index - b.index)[0].item;
  }

  if (hasCyrillicAny(name)) return normalizeSupplementalRussianPlaceName(name);
  return supplementalPlaceNameOverrides.get(name) ?? transliterateLatinPhrase(name || asciiName);
};

const placeTypeLabel = (featureCode) => {
  if (featureCode === "PPLC") return "Столица";
  if (featureCode.startsWith("PPLA")) return "Административный центр";
  if (featureCode === "PPLX") return "Район / часть города";
  if (featureCode === "PPLQ") return "Населенный пункт";
  if (featureCode === "PT") return "Поселок / точка";
  return "Населенный пункт";
};

const removeTextMarks = (value) => value.normalize("NFC").replace(/[\u0300-\u036f]/g, "");

const normalizeAdminTypeCase = (value) =>
  removeTextMarks(value)
    .replace(/\s+/g, " ")
    .trim()
    .replace(/Городской/g, "городской")
    .replace(/Муниципальный/g, "муниципальный")
    .replace(/Административный/g, "административный")
    .replace(/Округ/g, "округ")
    .replace(/Район/g, "район")
    .replace(/Улус/g, "улус")
    .replace(/Кожуун/g, "кожуун");

const districtNameOverrides = new Map([
  ["Rostov-on-Don", "Ростов-на-Дону"],
  ["Ростов на Дон", "Ростов-на-Дону"],
  ["городской округ Нижневарто", "городской округ Нижневартовск"],
  ["городской округ Нижний Таг", "городской округ Нижний Тагил"],
  ["Verkhny Ufaley", "Верхний Уфалей"],
  ["Верхнй Уфалей", "Верхний Уфалей"],
  ["городской округ Верхняя Пы", "городской округ Верхняя Пышма"],
  ["городской округ Верхняя Ту", "городской округ Верхняя Тура"],
  ["Нижнетуринский городской о", "Нижнетуринский городской округ"],
  // Районы UKR ADM2 в Запорожской и Херсонской, которых не оказалось в
  // ОКТМО: без явного имени они оставались транслитерационным мусором —
  // «Орихив», «Хулиаиполе», «Запорижиа». Русские имена дореформенных
  // районов, как их и пишут сводки.
  ["Bilmak", "Бильмакский район"],
  ["Vilniansk", "Вольнянский район"],
  ["Zaporizhia", "Запорожский район"],
  ["Kamianka Dniprovska", "Каменско-Днепровский район"],
  ["Novomykolaivka", "Новониколаевский район"],
  ["Orikhiv", "Ореховский район"],
  ["Huliaipole", "Гуляйпольский район"],
  ["Hornostaivka", "Горностаевский район"]
]);

const applyDistrictNameOverride = (value) => districtNameOverrides.get(value) ?? value;

const normalizeAdminLatin = (value) =>
  normalizeLatin(value)
    .replace(/gorodskoj/g, "gorodskoy")
    .replace(/rajon/g, "rayon")
    .replace(/skij/g, "skiy")
    .replace(/skiy(?=rayon|district|municipality|urban|gorodskoy|$)/g, "sky")
    .replace(/skiy$/g, "sky");

const unique = (values) => [...new Set(values.filter(Boolean))];

const splitAlternates = (value) =>
  String(value || "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);

const adminStem = (value) => {
  const clean = String(value || "")
    .replace(/[’`]/g, "'")
    .replace(/\s+/g, " ")
    .trim();
  const withoutRegionSuffix = clean.split(",")[0].trim();
  const prefixMatch = withoutRegionSuffix.match(/^(?:Gorodskoy Okrug|Gorodskoj Okrug|Urban Okrug|Urban District)\s+(.+)$/i);
  if (prefixMatch) return prefixMatch[1].trim();

  return withoutRegionSuffix
    .replace(
      /\s+(?:municipal region|municipal district|municipality|urban okrug|urban district|gorodskoy okrug|gorodskoj okrug|district|rayon|ulus|kozhuun)$/i,
      ""
    )
    .trim();
};

const adjectiveRayonVariants = (stem) => {
  if (!stem) return [];
  const compactStem = stem.replace(/\s+/g, " ").trim();
  const lower = compactStem.toLowerCase();
  if (/(sky|skiy|skij)$/i.test(compactStem)) {
    return [`${compactStem} Rayon`, `${compactStem} District`];
  }
  if (/(skaya|skoye|skoy)$/i.test(compactStem)) return [];

  const variants = [`${compactStem}sky Rayon`, `${compactStem}skiy Rayon`, `${compactStem}sky District`, `${compactStem}skiy District`];
  if (lower.endsWith("sk")) {
    variants.push(`${compactStem}y Rayon`, `${compactStem}iy Rayon`, `${compactStem}y District`, `${compactStem}iy District`);
  }
  return variants;
};

const adminNameVariants = (value) => {
  const clean = String(value || "")
    .replace(/[’`]/g, "'")
    .replace(/\s+/g, " ")
    .trim();
  if (!clean) return [];

  const withoutRegionSuffix = clean.split(",")[0].trim();
  const stem = adminStem(clean);
  const variants = [clean, withoutRegionSuffix];

  if (stem && stem !== clean) {
    variants.push(
      stem,
      `${stem} District`,
      `${stem} Rayon`,
      `${stem} municipal region`,
      `${stem} Municipality`,
      `${stem} Urban District`,
      `${stem} Urban Okrug`,
      `Gorodskoy Okrug ${stem}`,
      `Gorodskoj Okrug ${stem}`,
      ...adjectiveRayonVariants(stem)
    );
  }

  const cityPrefixMatch = withoutRegionSuffix.match(/^(?:Gorodskoy Okrug|Gorodskoj Okrug|Urban Okrug|Urban District)\s+(.+)$/i);
  if (cityPrefixMatch) {
    const city = cityPrefixMatch[1].trim();
    variants.push(city, `${city} Municipality`, `${city} Urban District`, `${city} Urban Okrug`);
  }

  if (/municipal region$/i.test(clean) && stem) {
    variants.push(`${stem}skiy Rayon`, `${stem}sky Rayon`, `${stem}skiy District`, `${stem}sky District`);
  }

  return unique(variants);
};

const districtLookupEntries = (value) => {
  const exact = unique([
    value,
    String(value || "")
      .replace(/[’`]/g, "'")
      .split(",")[0]
      .trim()
  ])
    .map((item) => normalizeAdminLatin(item))
    .filter(Boolean)
    .map((key) => ({ key, priority: 0 }));

  const derived = adminNameVariants(value)
    .map((item) => normalizeAdminLatin(item))
    .filter(Boolean)
    .filter((key) => !exact.some((entry) => entry.key === key))
    .map((key) => ({ key, priority: 6 }));

  return unique([...exact, ...derived].map((entry) => `${entry.key}|${entry.priority}`)).map((entry) => {
    const [key, priority] = entry.split("|");
    return { key, priority: Number(priority) };
  });
};

const russianAdminNameScore = (name, target, index) => {
  const lower = name.toLowerCase();
  const typeScore =
    lower.includes("городской округ") || lower.includes("муниципальный округ")
      ? 0
      : lower.includes("район") || lower.includes("улус") || lower.includes("кожуун")
        ? 1
        : 3;
  return typeScore * 1000 + (target ? levenshtein(normalizeAdminLatin(name), target) : index) + index / 100;
};

const pickRussianAdminName = (name, asciiName, alternates) => {
  const values = unique([name, asciiName, ...splitAlternates(alternates)])
    .map(removeTextMarks)
    .filter((item) => item.length > 1 && item.length <= 120 && hasCyrillic(item) && !/[A-Za-z]/.test(item));

  if (values.length === 0) return null;

  const target = normalizeAdminLatin(asciiName || name);
  return normalizeAdminTypeCase(
    values
      .map((item, index) => ({
        item,
        score: russianAdminNameScore(item, target, index)
      }))
      .sort((a, b) => a.score - b.score)[0].item
  );
};

const addDistrictNameLookup = (lookup, sourceName, russianName, exactPriority, derivedPriority) => {
  if (!sourceName || !russianName) return;

  for (const { key, priority } of districtLookupEntries(sourceName)) {
    const score = priority === 0 ? exactPriority : derivedPriority;
    const current = lookup.get(key);
    if (!current || score < current.score) {
      lookup.set(key, { name: russianName, score });
    }
  }
};

const buildDistrictNameLookup = (geonamesRaw) => {
  const lookup = new Map();
  const adminCodes = new Set(["ADM2", "ADM3", "ADM4", "ADMD"]);

  for (const line of geonamesRaw.split(/\r?\n/)) {
    if (!line) continue;

    const cols = line.split("\t");
    if (cols.length < 19) continue;

    const [name, asciiName, alternates, featureClass, featureCode] = [cols[1], cols[2], cols[3], cols[6], cols[7]];
    if (featureClass === "A" && adminCodes.has(featureCode)) {
      const russianName = pickRussianAdminName(name, asciiName, alternates);
      if (!russianName) continue;

      addDistrictNameLookup(lookup, name, russianName, 0, 18);
      addDistrictNameLookup(lookup, asciiName, russianName, 0, 18);
      for (const alternate of splitAlternates(alternates)) addDistrictNameLookup(lookup, alternate, russianName, 0, 18);
      addDistrictNameLookup(lookup, russianName, russianName, 0, 18);
    }

    if (featureClass === "P") {
      const russianName = pickPlaceName(name, asciiName, alternates);
      if (!russianName || !hasCyrillic(russianName) || /[A-Za-z]/.test(russianName)) continue;

      addDistrictNameLookup(lookup, name, russianName, 8, 40);
      addDistrictNameLookup(lookup, asciiName, russianName, 8, 40);
      for (const alternate of splitAlternates(alternates)) addDistrictNameLookup(lookup, alternate, russianName, 8, 40);
    }
  }

  return lookup;
};

const latinWordToCyrillic = (word) => {
  const endingRules = [
    [/skij$/i, "ский"],
    [/skiy$/i, "ский"],
    [/sky$/i, "ский"],
    [/skaya$/i, "ская"],
    [/skoye$/i, "ское"],
    [/skoy$/i, "ской"],
    [/tskij$/i, "цкий"],
    [/tskiy$/i, "цкий"],
    [/tsky$/i, "цкий"]
  ];

  for (const [pattern, replacement] of endingRules) {
    if (pattern.test(word)) {
      return capitalizeCyrillic(latinWordToCyrillic(word.replace(pattern, "")) + replacement);
    }
  }

  const replacements = [
    ["shch", "щ"],
    ["sch", "щ"],
    ["yo", "ё"],
    ["jo", "ё"],
    ["zh", "ж"],
    ["kh", "х"],
    ["ts", "ц"],
    ["ch", "ч"],
    ["sh", "ш"],
    ["yu", "ю"],
    ["ju", "ю"],
    ["ya", "я"],
    ["ja", "я"],
    ["ye", "е"],
    ["je", "е"]
  ];

  let rest = word.toLowerCase();
  let output = "";

  while (rest.length > 0) {
    const pair = replacements.find(([latin]) => rest.startsWith(latin));
    if (pair) {
      output += pair[1];
      rest = rest.slice(pair[0].length);
      continue;
    }

    const char = rest[0];
    output +=
      {
        a: "а",
        b: "б",
        c: "к",
        d: "д",
        e: "е",
        f: "ф",
        g: "г",
        h: "х",
        i: "и",
        j: "й",
        k: "к",
        l: "л",
        m: "м",
        n: "н",
        o: "о",
        p: "п",
        q: "к",
        r: "р",
        s: "с",
        t: "т",
        u: "у",
        v: "в",
        w: "в",
        x: "кс",
        y: "й",
        z: "з"
      }[char] ?? char;
    rest = rest.slice(1);
  }

  return capitalizeCyrillic(output);
};

const capitalizeCyrillic = (value) => (value ? value[0].toUpperCase() + value.slice(1) : value);

const transliterateLatinPhrase = (value) =>
  String(value || "")
    .replace(/closed administrative-territorial formation of/gi, "закрытое административно-территориальное образование")
    .split(/([-\s'])/)
    .map((part) => {
      if (!/[A-Za-z]/.test(part)) return part;
      return latinWordToCyrillic(part);
    })
    .join("")
    .replace(/\s+/g, " ")
    .trim();

const fallbackRussianDistrictName = (value) => {
  const clean = String(value || "")
    .replace(/[’`]/g, "'")
    .replace(/\s+/g, " ")
    .trim()
    .split(",")[0]
    .trim();

  const rules = [
    [/^(?:Gorodskoy Okrug|Gorodskoj Okrug|Urban Okrug|Urban District)\s+(.+)$/i, "городской округ"],
    [/\s+(?:Urban Okrug|Urban District)$/i, "городской округ"],
    [/\s+(?:Municipality)$/i, "муниципальный округ"],
    [/\s+(?:municipal region|Municipal District)$/i, "муниципальный район"],
    [/\s+(?:District|Rayon)$/i, "район"],
    [/\s+Ulus$/i, "улус"],
    [/\s+Kozhuun$/i, "кожуун"]
  ];

  for (const [pattern, typeLabel] of rules) {
    const match = clean.match(pattern);
    if (!match) continue;

    const stem = match[1] ?? clean.replace(pattern, "").trim();
    const russianStem = transliterateLatinPhrase(stem);
    return typeLabel === "городской округ" && match[1] ? `${typeLabel} ${russianStem}` : `${russianStem} ${typeLabel}`;
  }

  return transliterateLatinPhrase(clean);
};

const districtResultTypePenalty = (sourceName, resultName) => {
  const source = String(sourceName || "").toLowerCase();
  const result = String(resultName || "").toLowerCase();

  if (/(municipal region|district|rayon|ulus|kozhuun)$/.test(source)) {
    if (result.includes("район") || result.includes("улус") || result.includes("кожуун")) return 0;
    if (result.includes("округ")) return 5;
    return 2;
  }

  if (/(municipality|urban okrug|urban district)$/.test(source)) {
    if (result.includes("округ")) return 0;
    if (result.includes("район")) return 5;
    return 2;
  }

  return 0;
};

const resolveDistrictName = (name, lookup) => {
  const directOverride = districtNameOverrides.get(name);
  if (directOverride) return directOverride;

  if (hasCyrillic(name) && !/[A-Za-z]/.test(name)) return applyDistrictNameOverride(normalizeAdminTypeCase(name));

  const matches = districtLookupEntries(name)
    .map((entry) => {
      const value = lookup.get(entry.key);
      return value ? { ...value, score: value.score + entry.priority + districtResultTypePenalty(name, value.name) } : null;
    })
    .filter(Boolean)
    .sort((a, b) => a.score - b.score);

  return applyDistrictNameOverride(matches[0]?.name ?? fallbackRussianDistrictName(name));
};

const geonamesRaw = execFileSync("unzip", ["-p", join(root, "research/data_sources/geonames_RU.zip"), "RU.txt"], {
  encoding: "utf8",
  maxBuffer: 120 * 1024 * 1024
});

const districtNameLookup = buildDistrictNameLookup(geonamesRaw);

// Канонические имена по ISO 3166-2. Имеют приоритет над reference-словарем.
// RU-MOW — город Москва, RU-MOS — Московская область; в RadarMap reference
// эти два имени переставлены местами, из-за чего полигон города получал имя
// области и наоборот. Проверять по ISO, а не по названию источника.
const regionNameOverridesByIso = new Map([
  ["RU-MOW", "Москва"],
  ["RU-MOS", "Московская область"],
  ["RU-SPE", "Санкт-Петербург"],
  ["RU-LEN", "Ленинградская область"]
]);

const regionNameByIso = new Map(
  readJson("research/radarmap_reference/data/russia_regions.geojson").features.map((feature) => [
    feature.properties?.iso_3166_2,
    feature.properties?.name_ru
  ])
);
for (const [iso, name] of regionNameOverridesByIso) {
  regionNameByIso.set(iso, name);
}
const preferredPlaceNameByCoord = new Map(
  readJson("research/radarmap_reference/data/cities_ru.json")
    .filter((city) => Number.isFinite(city.lat) && Number.isFinite(city.lon))
    .map((city) => [`${roundNumber(city.lat, 5)}|${roundNumber(city.lon, 5)}`, city.name])
);

const supplementalPlaceAdmin1Codes = new Set(["05", "08", "11", "14", "20", "26"]);

const geoBoundariesAdm1 = readJson("research/data_sources/geoboundaries_RUS_ADM1_simplified.geojson");
const supplementalAdm1 = readJson("research/data_sources/supplemental_regions_admin1.geojson");
const regions = compactFeatureCollection(
  {
    type: "FeatureCollection",
    features: [...geoBoundariesAdm1.features, ...supplementalAdm1.features]
  },
  (props, index) => ({
    id: props.id ?? props.shapeID ?? `region-${index}`,
    name: props.name ?? regionNameByIso.get(props.shapeISO) ?? props.shapeName,
    iso: props.iso ?? props.shapeISO ?? null,
    // Акватория среди субъектов. Сообщения про Азовское море ложились на
    // сушу, названную рядом, а если рядом ничего не названо — терялись
    // вовсе. Признак нужен дальше по конвейеру: морю нельзя отдавать
    // прибрежные районы, а посадочную страницу «по районам» ему не собрать.
    ...(props.kind ? { kind: props.kind } : {})
  }),
  3
);
writeJson("regions.json", regions);

const matchesRegionPoint = createRegionPointMatcher(regions);
const regionBoundsList = regions.features.map((feature) => geometryBounds(feature.geometry));
const regionCoordinatePoints = regions.features.flatMap((feature) => getCoordinatePoints(feature.geometry?.coordinates));
const bboxTouchesMapArea = (bbox, padding = 0.2) => Boolean(bbox) && regionBoundsList.some((regionBounds) => boundsTouch(bbox, regionBounds, padding));
const featureTouchesMapArea = (feature) => {
  const bounds = geometryBounds(feature.geometry);
  if (!bboxTouchesMapArea(bounds)) return false;
  return (
    featureTouchesRegions(feature, matchesRegionPoint) ||
    regionCoordinatePoints.some((point) => boundsTouch(bounds, [point[0], point[1], point[0], point[1]], 0) && geometryContainsPoint(feature.geometry, point))
  );
};
const hydroName = (props) => props.name_ru || props.name || props.name_en || props.label || "";
const compactHydroProperties = (prefix) => (props, index) => ({
  id: `${prefix}-${props.ne_id ?? props.dissolve ?? props.rivernum ?? index}`,
  name: hydroName(props),
  featureClass: props.featurecla || null,
  scalerank: Number.isFinite(Number(props.scalerank)) ? Number(props.scalerank) : null,
  minZoom: Number.isFinite(Number(props.min_zoom)) ? Number(props.min_zoom) : null,
  minLabel: Number.isFinite(Number(props.min_label)) ? Number(props.min_label) : null
});

const waterBodiesSource = readJson("research/data_sources/ne_10m_lakes.geojson");
const waterBodies = compactFeatureCollection(
  {
    type: "FeatureCollection",
    features: waterBodiesSource.features.filter((feature) => featureTouchesRegions(feature, matchesRegionPoint))
  },
  compactHydroProperties("water"),
  3
);
writeJson("water-bodies.json", waterBodies);

const riversSource = readJson("research/data_sources/ne_10m_rivers_lake_centerlines.geojson");
const rivers = compactFeatureCollection(
  {
    type: "FeatureCollection",
    features: riversSource.features.filter((feature) => featureTouchesRegions(feature, matchesRegionPoint))
  },
  compactHydroProperties("river"),
  3
);
writeJson("rivers.json", rivers);

const hydroRiverNetworkSource = readJson("research/data_sources/hydrorivers_russia_network.geojson");
const riverNetworkMajor = compactFeatureCollection(
  {
    type: "FeatureCollection",
    features: hydroRiverNetworkSource.features.filter((feature) => Number(feature.properties?.minZoom ?? 99) <= 6.6)
  },
  (props, index) => ({
    id: props.id ?? `river-network-major-${index}`,
    minZoom: props.minZoom,
    widthClass: props.widthClass,
    lineCount: props.lineCount
  }),
  4
);
writeJson("river-network-major.json", riverNetworkMajor);

const riverNetworkDetail = compactFeatureCollection(
  {
    type: "FeatureCollection",
    features: hydroRiverNetworkSource.features.filter((feature) => Number(feature.properties?.minZoom ?? 0) > 6.6)
  },
  (props, index) => ({
    id: props.id ?? `river-network-detail-${index}`,
    minZoom: props.minZoom,
    widthClass: props.widthClass,
    lineCount: props.lineCount
  }),
  4
);
writeJson("river-network-detail.json", riverNetworkDetail);

const naturalEarthUrbanAreas = readShapefileFromZip(root, {
  zipName: "ne_10m_urban_areas.zip",
  baseName: "ne_10m_urban_areas",
  coordinatePrecision: 3,
  filterRecord: ({ bbox }) => bboxTouchesMapArea(bbox)
});
const urbanAreas = compactFeatureCollection(
  {
    type: "FeatureCollection",
    features: naturalEarthUrbanAreas.features.filter((feature) => featureTouchesMapArea(feature))
  },
  (props, index) => {
    const areaSqKm = finiteNumber(props.area_sqkm, 0);
    const sourceMinZoom = finiteNumber(props.min_zoom, 5.8);
    const minZoom =
      areaSqKm >= 450 ? 4.15 : areaSqKm >= 120 ? 4.55 : areaSqKm >= 40 ? 5.05 : Math.max(5.45, sourceMinZoom - 0.45);

    return {
      id: `urban-${index}`,
      featureClass: props.featurecla || "Urban area",
      areaSqKm,
      scalerank: finiteNumber(props.scalerank, 9),
      minZoom
    };
  },
  3
);
writeJson("urban-areas.json", urbanAreas);

const naturalEarthRoads = readShapefileFromZip(root, {
  zipName: "ne_10m_roads.zip",
  baseName: "ne_10m_roads",
  coordinatePrecision: 3,
  maxBuffer: 180 * 1024 * 1024,
  filterRecord: ({ bbox, properties }) => bboxTouchesMapArea(bbox) && finiteNumber(properties.length_km, 0) >= 5
});
const roads = compactFeatureCollection(
  {
    type: "FeatureCollection",
    features: naturalEarthRoads.features
      .filter((feature) => featureTouchesMapArea(feature))
      .filter((feature) => finiteNumber(feature.properties?.length_km, 0) >= 5)
  },
  (props, index) => ({
    id: `road-${index}`,
    name: props.label || props.name || props.namealt || "",
    type: props.type || "Road",
    level: props.level || null,
    scalerank: finiteNumber(props.scalerank, 9),
    lengthKm: finiteNumber(props.length_km, 0),
    expressway: finiteNumber(props.expressway, 0) === 1,
    minZoom: Math.max(4.65, finiteNumber(props.min_zoom, 6.4) - 0.45),
    minLabel: Math.max(6.9, finiteNumber(props.min_label, 8.6) - 0.25)
  }),
  3
);
roads.features.push({
  type: "Feature",
  id: "road-crimean-bridge",
  properties: {
    id: "road-crimean-bridge",
    name: "Крымский мост",
    type: "Bridge",
    level: "major",
    scalerank: 2,
    lengthKm: 19,
    expressway: true,
    minZoom: 4.2,
    minLabel: 5.4,
    synthetic: true
  },
  geometry: {
    type: "LineString",
    coordinates: [
      [36.476, 45.356],
      [36.515, 45.334],
      [36.553, 45.303],
      [36.592, 45.274],
      [36.635, 45.25],
      [36.681, 45.233],
      [36.722, 45.225]
    ]
  }
});
writeJson("roads.json", roads);

const naturalEarthRailways = readShapefileFromZip(root, {
  zipName: "ne_10m_railroads.zip",
  baseName: "ne_10m_railroads",
  coordinatePrecision: 3,
  maxBuffer: 180 * 1024 * 1024,
  filterRecord: ({ bbox }) => bboxTouchesMapArea(bbox)
});
const railways = compactFeatureCollection(
  {
    type: "FeatureCollection",
    features: naturalEarthRailways.features.filter((feature) => featureTouchesMapArea(feature))
  },
  (props, index) => {
    const scalerank = finiteNumber(props.scalerank, 9);
    const category = finiteNumber(props.category, 2);
    const nationalScale = finiteNumber(props.natlscale, 20);
    const major = scalerank <= 6 || category <= 1 || nationalScale <= 10;

    return {
      id: `railway-${props.rwdb_rr_id ?? index}`,
      type: props.featurecla || "Railroad",
      scalerank,
      category,
      nationalScale,
      electric: finiteNumber(props.electric, 0) === 1,
      multiTrack: finiteNumber(props.mult_track, 0) === 1,
      minZoom: major ? 5.55 : 7.35
    };
  },
  3
);
writeJson("railways.json", railways);

const geographyName = (props) => props.NAME_RU || props.NAME || props.NAME_EN || props.LABEL || "";
const terrainClasses = new Set(["Range/mtn", "Plateau", "Foothills", "Valley", "Gorge", "Basin", "Plain", "Lowland", "Depression", "Delta"]);
const terrainKind = (featureClass) => {
  if (featureClass === "Range/mtn" || featureClass === "Foothills" || featureClass === "Gorge") return "mountain";
  if (featureClass === "Plateau") return "plateau";
  if (featureClass === "Basin" || featureClass === "Lowland" || featureClass === "Depression") return "lowland";
  if (featureClass === "Delta") return "delta";
  return "plain";
};
const geographyRegions = readShapefileFromZip(root, {
  zipName: "ne_10m_geography_regions_polys.zip",
  baseName: "ne_10m_geography_regions_polys",
  coordinatePrecision: 3,
  filterRecord: ({ bbox, properties }) => terrainClasses.has(String(properties.FEATURECLA ?? "")) && bboxTouchesMapArea(bbox)
});
const terrainRegions = compactFeatureCollection(
  {
    type: "FeatureCollection",
    features: geographyRegions.features.filter(
      (feature) => terrainClasses.has(String(feature.properties?.FEATURECLA ?? "")) && featureTouchesMapArea(feature)
    )
  },
  (props, index) => ({
    id: `terrain-${props.NE_ID ?? index}`,
    name: geographyName(props),
    featureClass: props.FEATURECLA || null,
    terrainKind: terrainKind(String(props.FEATURECLA ?? "")),
    scalerank: finiteNumber(props.SCALERANK, 8),
    minLabel: finiteNumber(props.MIN_LABEL, 6)
  }),
  3
);
writeJson("terrain-regions.json", terrainRegions);

const naturalEarthGlaciers = readShapefileFromZip(root, {
  zipName: "ne_10m_glaciated_areas.zip",
  baseName: "ne_10m_glaciated_areas",
  coordinatePrecision: 3,
  filterRecord: ({ bbox }) => bboxTouchesMapArea(bbox)
});
const glaciers = compactFeatureCollection(
  {
    type: "FeatureCollection",
    features: naturalEarthGlaciers.features.filter((feature) => featureTouchesMapArea(feature))
  },
  (props, index) => ({
    id: `glacier-${props.recnum ?? index}`,
    name: props.name || "",
    scalerank: finiteNumber(props.scalerank, 8),
    minZoom: finiteNumber(props.min_zoom, 5.7)
  }),
  3
);
writeJson("glaciers.json", glaciers);

const landCoverKind = (biomeName, ecoName) => {
  const value = `${biomeName ?? ""} ${ecoName ?? ""}`.toLowerCase();
  if (value.includes("flooded") || value.includes("mangrove") || value.includes("wetland") || value.includes("bog") || value.includes("marsh")) {
    return "wetland";
  }
  if (value.includes("tundra")) return "tundra";
  if (value.includes("forest") || value.includes("taiga")) return "forest";
  return null;
};
const ecoregionsPath = join(root, "research/data_sources/Ecoregions2017.zip");
const ecoregions = existsSync(ecoregionsPath)
  ? readShapefileFromZip(root, {
      zipName: "Ecoregions2017.zip",
      baseName: "Ecoregions2017",
      coordinatePrecision: 3,
      maxBuffer: 900 * 1024 * 1024,
      filterRecord: ({ bbox, properties }) => Boolean(landCoverKind(properties.BIOME_NAME, properties.ECO_NAME)) && bboxTouchesMapArea(bbox)
    })
  : { type: "FeatureCollection", features: [] };
const landCover = compactFeatureCollection(
  {
    type: "FeatureCollection",
    features: ecoregions.features.filter((feature) => landCoverKind(feature.properties?.BIOME_NAME, feature.properties?.ECO_NAME) && featureTouchesMapArea(feature))
  },
  (props, index) => ({
    id: `land-cover-${props.ECO_ID ?? index}`,
    name: props.ECO_NAME || "",
    biome: props.BIOME_NAME || "",
    landCoverKind: landCoverKind(props.BIOME_NAME, props.ECO_NAME),
    realm: props.REALM || null,
    areaSqDeg: finiteNumber(props.Shape_Area, null)
  }),
  3
);
writeJson("land-cover.json", landCover);

const geoBoundariesAdm2 = readJson("research/data_sources/geoboundaries_RUS_ADM2_simplified.geojson");

// Районы регионов, которых в наборе RUS нет вовсе: Крым, Севастополь, ДНР,
// ЛНР, Херсонская и Запорожская области. Сами регионы добавлены отдельным
// файлом supplemental_regions_admin1, а районов у них не было ни одного —
// шесть с половиной тысяч НП висели прямо под регионом, и сообщение про
// Бахчисарайский район не находило зоны, а карта не могла закрасить район.
//
// Берётся набор UKR ADM2 за 2006 год: он повторяет доreform-ную сетку районов,
// а она совпадает с нынешней российской заметно лучше, чем украинская реформа
// 2020 года, укрупнившая районы втрое.
const supplementalRegionNames = new Set([
  "Республика Крым",
  "Севастополь",
  "Донецкая Народная Республика",
  "Луганская Народная Республика",
  "Херсонская область",
  "Запорожская область"
]);
const supplementalRegionFeatures = regions.features.filter((feature) =>
  supplementalRegionNames.has(feature.properties?.name)
);
const matchesSupplementalPoint = createRegionPointMatcher({
  type: "FeatureCollection",
  features: supplementalRegionFeatures
});
// Доля точек контура, которая должна лежать внутри наших регионов. Район на
// границе принадлежит той стороне, где лежит его основная часть; половины
// мало — приграничный украинский район зашёл бы наравне со своим.
const supplementalShare = 0.6;
const insideSupplementalRegions = (feature) => {
  const points = getCoordinatePoints(feature.geometry?.coordinates);
  if (!points.length) return false;
  let hits = 0;
  for (const point of points) if (matchesSupplementalPoint(point)) hits += 1;
  return hits / points.length >= supplementalShare;
};

const geoBoundariesUkrAdm2 = readJson("research/data_sources/geoboundaries_UKR_ADM2_simplified.geojson");
const supplementalAdm2 = geoBoundariesUkrAdm2.features.filter(insideSupplementalRegions);

const districts = compactFeatureCollection(
  {
    type: "FeatureCollection",
    features: [...geoBoundariesAdm2.features, ...supplementalAdm2]
  },
  (props, index) => ({
    id: props.shapeID ?? `district-${index}`,
    name: resolveDistrictName(props.shapeName, districtNameLookup),
    iso: props.shapeISO || null
  }),
  3
);
writeJson("districts.json", districts);

const geonamesUaRaw = execFileSync("unzip", ["-p", join(root, "research/data_sources/geonames_UA.zip"), "UA.txt"], {
  encoding: "utf8",
  maxBuffer: 24 * 1024 * 1024
});

const parseGeoNamesPlaces = (raw, shouldInclude, pickName = pickPlaceName) =>
  raw
  .split(/\r?\n/)
  .filter(Boolean)
  .flatMap((line) => {
    const cols = line.split("\t");
    if (cols.length < 19 || cols[6] !== "P") return [];
    if (!shouldInclude(cols)) return [];

    const lat = Number(cols[4]);
    const lon = Number(cols[5]);
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) return [];

    const population = Number(cols[14]);
    const roundedLat = roundNumber(lat, 5);
    const roundedLon = roundNumber(lon, 5);
    const preferredName = preferredPlaceNameByCoord.get(`${roundedLat}|${roundedLon}`);
    const name = preferredName ?? pickName(cols[1], cols[2], cols[3]);
    const asciiName = cols[2] && cols[2] !== name ? cols[2] : "";
    const featureCode = cols[7];

    return [
      [
        cols[0],
        name,
        asciiName,
        roundedLat,
        roundedLon,
        Number.isFinite(population) && population > 0 ? population : null,
        featureCode,
        placeTypeLabel(featureCode)
      ]
    ];
  });

const places = [
  ...parseGeoNamesPlaces(geonamesRaw, () => true),
  ...parseGeoNamesPlaces(geonamesUaRaw, (cols) => supplementalPlaceAdmin1Codes.has(cols[10]), pickSupplementalRussianPlaceName)
]
  .sort((a, b) => (b[5] ?? 0) - (a[5] ?? 0) || String(a[1]).localeCompare(String(b[1]), "ru"));

writeJson("places.json", {
  fields: ["id", "name", "asciiName", "lat", "lon", "population", "featureCode", "typeLabel"],
  rows: places
});

// Русские подписи крупных городов для карты. Подложка используется без
// собственных подписей (латиница CARTO на русской карте обстановки
// выглядела чужой), поэтому ориентиры даёт этот слой: имя, точка и
// население — ярусы появления считает клиент. Сотня тысяч жителей —
// порог, ниже которого город на обзорной карте страны не ориентир.
const cityLabels = places
  .filter((row) => (row[5] ?? 0) >= 100_000)
  .map((row) => ({ name: row[1], lat: row[3], lon: row[4], population: row[5] }));
writeJson("city-labels.json", { cities: cityLabels });

const oktmoCsv = readFileSync(
  join(root, "research/data_sources/rosstat_oktmo_data_20260601T1406.csv"),
  "utf8"
);
const oktmoRows = oktmoCsv.split(/\r?\n/).filter(Boolean).length;

const summary = {
  generatedAt: new Date().toISOString(),
  regions: regions.features.length,
  districts: districts.features.length,
  rivers: rivers.features.length,
  riverNetworkMajor: riverNetworkMajor.features.length,
  riverNetworkDetail: riverNetworkDetail.features.length,
  waterBodies: waterBodies.features.length,
  landCover: landCover.features.length,
  terrainRegions: terrainRegions.features.length,
  glaciers: glaciers.features.length,
  urbanAreas: urbanAreas.features.length,
  roads: roads.features.length,
  railways: railways.features.length,
  places: places.length,
  oktmoRows
};
writeJson("summary.json", summary);

console.log(
  `Prepared map data: ${summary.regions} regions, ${summary.districts} districts, ${summary.places} places, ${summary.rivers} named rivers, ${summary.riverNetworkMajor + summary.riverNetworkDetail} river-network groups, ${summary.waterBodies} water bodies, ${summary.landCover} land-cover areas, ${summary.terrainRegions} terrain regions, ${summary.glaciers} glaciers, ${summary.urbanAreas} urban areas, ${summary.roads} roads, ${summary.railways} railways`
);

// Штамп пишется последним: упавшая на середине пересборка не должна
// прикидываться выполненной.
writeFileSync(stampPath, JSON.stringify({ inputs: inputsStamp }));

// После пересборки полигоны стоят без имён и родителей из справочника —
// их дописывает pipeline.gazetteer. Напоминание, а не автоматика: скрипту
// данных нечего лезть в чужую базу.
console.log("Не забудьте: ingest/.venv/bin/python -m pipeline.gazetteer — вернуть полигонам имена справочника.");
