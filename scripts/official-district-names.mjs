import {
  levenshtein,
  normalizeLatin,
  parseSemicolonCsvRow
} from "./official-place-names.mjs";

const COLLECTION_HEADING = /^(?:Муниципальные|Городские|Внутригородские|Сельские)(?:\s|$)/i;
const PLACE_TYPE_PREFIX = /^(?:г|пгт|рп|п|с|ст-ца|ст|нп)\.?\s+/i;

const clean = (value) => String(value || "")
  .normalize("NFC")
  .replace(/[«»"]/g, "")
  .replace(/\s+/g, " ")
  .trim();

const cleanCenter = (value) => clean(value).replace(PLACE_TYPE_PREFIX, "").trim();

const districtKind = (code, name) => {
  const value = clean(name).toLowerCase();
  if (code?.startsWith("7") || value.startsWith("город ") || value.includes("городской округ")) return "urban";
  if (code?.startsWith("6") || value.includes("муниципальный район")) return "district";
  if (code?.startsWith("5") || value.includes("муниципальный округ")) return "municipal";
  return "other";
};

const officialStem = (name) => clean(name)
  .replace(/^муниципальный округ\s+(?:город(?:-курорт)?\s+)?/i, "")
  .replace(/^муниципальный район\s+/i, "")
  .replace(/^городской округ\s+(?:город\s+)?/i, "")
  .replace(/^город(?:-(?:курорт|герой))?\s+/i, "")
  .replace(/^ЗАТО\s+/i, "")
  .replace(/\s+муниципальный (?:район|округ)$/i, "")
  .replace(/\s+округ$/i, "")
  .replace(/\s+район$/i, "")
  .trim();

const sourceKind = (value) => {
  const source = clean(value).toLowerCase();
  if (/(?:kozhuun|кожуун)/i.test(source)) return "kozhuun";
  if (/(?:ulus|улус)/i.test(source)) return "ulus";
  if (/(?:urban|okrug|city|gorodsk|городск|городское|зато|closed administrative)/i.test(source)) return "urban";
  if (/(?:district|rayon|region|район)/i.test(source)) return "district";
  return "bare";
};

const adminCore = (value) => clean(value)
  .replace(/\([^)]*(?:municipal formation|муниципальн)[^)]*\)/gi, " ")
  .replace(/^resort town of\s+/i, "")
  .replace(/^(?:gorodskoy okrug|gorodskoj okrug|urban okrug|urban district)\s+/i, "")
  .replace(/\s+(?:municipal region|municipal district|municipality|city district|district|rayon|region|city|ulus|kozhuun)$/i, "")
  .replace(/^(?:городской округ|муниципальный округ(?: город(?:-курорт)?)?|город(?:-курорт)?|ЗАТО)\s+/i, "")
  .replace(/^закрытое административно-территориальное образование\s+/i, "")
  .replace(/\s+район и город$/i, "")
  .replace(/\s+(?:муниципальный район|муниципальный округ|район)$/i, "")
  .replace(/\s+(?:улус|кожуун)$/i, "")
  .replace(/\s+(?:городск\S*|городское)\s+(?:о(?:к(?:р(?:у(?:г)?)?)?)?|мун\S*)$/i, "")
  .replace(/\s+городской$/i, "")
  .trim();

const adminKey = (value) => normalizeLatin(adminCore(value))
  .replace(/(?:skiy|skij|sky)$/g, "sk")
  .replace(/yo/g, "e");

const rootKey = (value) => adminKey(value).replace(/sk$/g, "");
const cyrillicAdminExactKey = (value) => adminCore(value)
  .toLowerCase()
  .replace(/[^а-яё0-9]+/g, "");
const cyrillicAdminKey = (value) => cyrillicAdminExactKey(value).replace(/ё/g, "е");

const cyrillicSemanticKey = (value) => cyrillicAdminKey(value)
  .replace(/(?:ий|ый|ая|ое|ые)$/i, "");

const displayName = (candidate, requestedKind, currentName, sourceName) => {
  const sourceStem = adminCore(sourceName);
  const stem = /ё/i.test(sourceStem)
    && cyrillicSemanticKey(sourceStem) === cyrillicSemanticKey(candidate.stem)
    ? sourceStem
    : candidate.stem;
  if (!stem) return candidate.name;
  const current = clean(currentName);
  if (/^ЗАТО(?:\s|$)/i.test(current) || /^ЗАТО(?:\s|$)/i.test(candidate.name)) return `ЗАТО ${stem}`;

  if (/кожуун$/i.test(current) || requestedKind === "kozhuun") return `${stem} кожуун`;
  if (/улус$/i.test(current) || requestedKind === "ulus") return `${stem} улус`;
  if (/^район имени\s+/i.test(current) || /^имени\s+/i.test(stem)) {
    return `район ${stem}`;
  }
  if (/^городской округ\s+/i.test(current) || requestedKind === "urban") {
    return `городской округ ${stem}`;
  }
  if (/\s+городской окру\S*$/i.test(current)) return `${stem} городской округ`;
  if (/\s+район$/i.test(current) || requestedKind === "district") {
    if (candidate.kind === "urban") {
      return candidate.center && rootKey(stem) !== rootKey(candidate.center)
        ? `${stem} городской округ`
        : `городской округ ${candidate.center || stem}`;
    }
    if (candidate.kind === "municipal") {
      return /^муниципальный округ\s+город(?:-курорт)?\s+/i.test(candidate.name)
        ? `муниципальный округ город${/город-курорт/i.test(candidate.name) ? "-курорт" : ""} ${candidate.center || stem}`
        : `${stem} район`;
    }
    return `${stem} район`;
  }

  if (candidate.kind === "district") return `${stem} район`;
  if (candidate.kind === "municipal") return `${stem} муниципальный округ`;
  return candidate.kind === "urban" ? (candidate.center || stem) : stem;
};

const uniqueAliases = (values) => {
  const result = new Map();
  for (const [value, weight] of values) {
    const key = adminKey(value);
    if (key && (!result.has(key) || weight < result.get(key))) result.set(key, weight);
    const root = rootKey(value);
    if (root && root !== key && (!result.has(root) || weight + 2 < result.get(root))) {
      result.set(root, weight + 2);
    }
  }
  return [...result.entries()].map(([key, weight]) => ({ key, weight }));
};

export const buildOfficialDistrictRegistry = (csvRaw) => {
  const candidates = [];
  const byTerritory = new Map();
  for (const line of String(csvRaw || "").split(/\r?\n/)) {
    if (!line) continue;
    const row = parseSemicolonCsvRow(line);
    const [territory, code, subcode, locality, , section, rawName, rawCenter] = row;
    const name = clean(rawName);
    if (
      section !== "1"
      || !/^\d{2}$/.test(territory)
      || code === "000"
      || subcode !== "000"
      || locality !== "000"
      || !name
      || COLLECTION_HEADING.test(name)
      || name.endsWith("/")
    ) continue;

    const center = cleanCenter(rawCenter);
    const kind = districtKind(code, name);
    const stem = officialStem(name);
    const candidate = {
      territory,
      code,
      name,
      center,
      kind,
      stem,
      aliases: uniqueAliases([
        [name, 0],
        [stem, 0],
        [center, 14]
      ])
    };
    candidates.push(candidate);
    if (!byTerritory.has(territory)) byTerritory.set(territory, []);
    byTerritory.get(territory).push(candidate);
  }
  return { candidates, byTerritory };
};

const kindPenalty = (requested, candidate) => {
  // geoBoundaries называет District и городские округа, поэтому тип — лишь
  // дополнительный сигнал. Точное имя должно быть сильнее типа источника.
  if (["district", "ulus", "kozhuun"].includes(requested)) return candidate.kind === "urban" ? 8 : 0;
  if (requested === "urban") return candidate.kind === "district" ? 12 : 0;
  if (requested === "bare") {
    if (candidate.kind === "urban") return 0;
    if (candidate.kind === "district") return 4;
    if (candidate.kind === "municipal") return 6;
  }
  return 0;
};

const keyDistanceScore = (source, candidate) => {
  if (!source || !candidate) return Infinity;
  if (source === candidate) return 0;

  const shorter = source.length <= candidate.length ? source : candidate;
  const longer = source.length > candidate.length ? source : candidate;
  const extra = longer.length - shorter.length;
  if (shorter.length >= 5 && longer.startsWith(shorter) && extra <= 12) return 5 + extra;

  const distance = levenshtein(source, candidate);
  const limit = Math.min(3, Math.max(1, Math.floor(Math.max(source.length, candidate.length) / 7)));
  return distance <= limit ? 12 + distance * 4 : Infinity;
};

export const resolveOfficialDistrictName = ({
  sourceName,
  currentName,
  fallbackName = currentName,
  registry,
  territory = null
}) => {
  const requestedKind = sourceKind(sourceName || currentName);
  const candidates = territory
    ? (registry.byTerritory.get(territory) ?? [])
    : registry.candidates;
  const findBest = (values) => {
    const keys = [...new Set(values.flatMap((value) => [adminKey(value), rootKey(value)]).filter(Boolean))];
    let match = null;
    for (const candidate of candidates) {
      for (const alias of candidate.aliases) {
        for (const key of keys) {
          const distance = keyDistanceScore(key, alias.key);
          if (!Number.isFinite(distance)) continue;
          const score = distance + alias.weight + kindPenalty(requestedKind, candidate);
          if (!match || score < match.score) {
            match = { candidate, score, distance, aliasWeight: alias.weight };
          }
        }
      }
    }
    return match;
  };

  // shapeName однозначно описывает сам полигон. GeoNames-имя — только
  // запасной вариант: глобальный поиск иногда возвращает соседний объект.
  const sourceMatch = findBest([sourceName]);
  const best = sourceMatch?.score <= 24 ? sourceMatch : findBest([currentName]);

  // Выше этого порога совпадение уже может быть одноимённым объектом из
  // другого субъекта. В таком случае безопаснее сохранить GeoNames-вариант.
  if (!best || best.score > 24) return currentName;

  const currentExactCyrillic = cyrillicAdminExactKey(currentName);
  const candidateExactCyrillic = cyrillicAdminExactKey(best.candidate.stem);
  const currentCyrillic = cyrillicAdminKey(currentName);
  const candidateCyrillic = cyrillicAdminKey(best.candidate.stem);
  const currentKey = adminKey(currentName);
  const currentSemantic = cyrillicSemanticKey(currentName);
  const currentEquivalent = [best.candidate.stem, best.candidate.center]
    .filter(Boolean)
    .some((value) => cyrillicSemanticKey(value) === currentSemantic)
    || currentExactCyrillic === candidateExactCyrillic
    || currentCyrillic === candidateCyrillic;
  const source = clean(sourceName);
  const sourceKey = adminKey(sourceName);
  const candidateSourceKeys = [best.candidate.stem, best.candidate.center]
    .map(adminKey)
    .filter(Boolean);
  const likelyTruncated = /[А-Яа-яЁё]/.test(source) && (
    /\b(?:о|ок|окр|окру|му|мун|муни|муниц|городск)$/i.test(source)
    || candidateSourceKeys.some((key) =>
      sourceKey.length >= 5 && key.startsWith(sourceKey) && key.length > sourceKey.length
    )
  );
  const malformedCurrent = /^городской округ\s+город\s+/i.test(clean(currentName));
  if (currentEquivalent && !likelyTruncated && !malformedCurrent) return currentName;

  if (
    requestedKind === "urban"
    && best.candidate.kind === "district"
    && !likelyTruncated
  ) return currentName;
  if (
    requestedKind === "district"
    && best.candidate.kind === "urban"
    && !currentEquivalent
  ) return fallbackName;

  const exactOfficialSource = best.aliasWeight <= 2 && best.distance === 0;
  const strongOfficialSource = best.aliasWeight <= 2 && best.distance <= 16;
  const truncatedOfficialSource = likelyTruncated
    && best.aliasWeight <= 2
    && best.distance <= 17;
  const currentDistance = currentCyrillic && candidateCyrillic
    ? levenshtein(currentCyrillic, candidateCyrillic)
    : Infinity;
  const nearCurrentTypo = currentDistance <= (candidateCyrillic.length >= 7 ? 2 : 1);
  const sourceCurrentExact = adminKey(sourceName) === currentKey;
  const replacesWrongLookup = !sourceCurrentExact && (
    exactOfficialSource
    || (requestedKind === "urban" && strongOfficialSource)
    || (requestedKind === "urban" && best.aliasWeight <= 14 && best.distance === 0)
  );
  if (!truncatedOfficialSource && !nearCurrentTypo && !replacesWrongLookup) return currentName;

  return displayName(best.candidate, requestedKind, currentName, sourceName);
};

// geoBoundaries UKR ADM2 хранит дореформенные районы как bare-name и не даёт
// языка. Фиксируем весь фактически используемый набор по стабильному source
// name: это исключает машинную транслитерацию и выбор одноимённого села.
export const supplementalDistrictNames = new Map([
  ["Bakhchysarai", "Бахчисарайский район"],
  ["Simferopol", "Симферопольский район"],
  ["Bilohirsk", "Белогорский район"],
  ["Kirovske", "Кировский район"],
  ["Lenine", "Ленинский район"],
  ["Chornomorske", "Черноморский район"],
  ["Sovietsky", "Советский район"],
  ["Nyzhniohirskyi", "Нижнегорский район"],
  ["Krasnohvardiiske", "Красногвардейский район"],
  ["Rozdolne", "Раздольненский район"],
  ["Dzhankoy", "Джанкойский район"],
  ["Pervomaiske", "Первомайский район"],
  ["Krasnoperekopsk", "Красноперекопский район"],
  ["Kalanchak", "Каланчакский район"],
  ["Skadovsk", "Скадовский район"],
  ["Holo Prystan", "Голопристанский район"],
  ["Oleshky", "Алешкинский район"],
  ["Chaplynka", "Чаплынский район"],
  ["Kakhovka", "Каховский район"],
  ["Novotroitske", "Новотроицкий район"],
  ["Henichesk", "Генический район"],
  ["Ivanivka", "Ивановский район"],
  ["Yakymivka", "Акимовский район"],
  ["Nyzhni Sirohozy", "Нижнесерогозский район"],
  ["Bilozerka", "Белозерский район"],
  ["Beryslav", "Бериславский район"],
  ["Hornostaivka", "Горностаевский район"],
  ["Pryazovske", "Приазовский район"],
  ["Velyka Oleksandrivka", "Великоалександровский район"],
  ["Velyka Lepetkya", "Великолепетихский район"],
  ["Verkhniy Rohackyk", "Верхнерогачикский район"],
  ["Novovorontsovka", "Нововоронцовский район"],
  ["Vysokopillia", "Высокопольский район"],
  ["Kamianka Dniprovska", "Каменско-Днепровский район"],
  ["Velyka Bilozerka", "Великобелозерский район"],
  ["Vesele", "Веселовский район"],
  ["Melitopol", "Мелитопольский район"],
  ["Vasylivka", "Васильевский район"],
  ["Mykhailivka", "Михайловский район"],
  ["Zaporizhia", "Запорожский район"],
  ["Tokmak", "Токмакский район"],
  ["Prymorsk", "Приморский район"],
  ["Chernihivka", "Черниговский район"],
  ["Berdiansk", "Бердянский район"],
  ["Bilmak", "Куйбышевский район"],
  ["Rozivka", "Розовский район"],
  ["Polohy", "Пологовский район"],
  ["Orikhiv", "Ореховский район"],
  ["Huliaipole", "Гуляйпольский район"],
  ["Vilniansk", "Вольнянский район"],
  ["Novomykolaivka", "Новониколаевский район"],
  ["Manhush", "Мангушский район"],
  ["Novoazovsk", "Новоазовский район"],
  ["Nikolske", "Володарский район"],
  ["Boikivske", "Тельмановский район"],
  ["Volnovakha", "Волновахский район"],
  ["Velyka Novosilka", "Великоновоселковский район"],
  ["Starobesheve", "Старобешевский район"],
  ["Marinka", "Марьинский район"],
  ["Amvrosiivka", "Амвросиевский район"],
  ["Shakhtarsk", "Шахтерский район"],
  ["Yasynuvata", "Ясиноватский район"],
  ["Pokrovsk", "Красноармейский район"],
  ["Dobropillia", "Добропольский район"],
  ["Kostiantynivka", "Константиновский район"],
  ["Bakhmut", "Артемовский район"],
  ["Oleksandrivka", "Александровский район"],
  ["Sloviansk", "Славянский район"],
  ["Lyman", "Краснолиманский район"],
  ["Antratsyt", "Антрацитовский район"],
  ["Dovzhansk", "Свердловский район"],
  ["Sorokyne", "Краснодонский район"],
  ["Stanytsia-Luhanska", "Станично-Луганский район"],
  ["Lutuhyne", "Лутугинский район"],
  ["Perevalsk", "Перевальский район"],
  ["Slovianoserbsk", "Славяносербский район"],
  ["Novoaidar", "Новоайдарский район"],
  ["Kreminna", "Кременской район"],
  ["Svatove", "Сватовский район"],
  ["Troitske", "Троицкий район"],
  ["Starobilsk", "Старобельский район"],
  ["Bilovodsk", "Беловодский район"],
  ["Bilokurakyne", "Белокуракинский район"],
  ["Novopskov", "Новопсковский район"],
  ["Markivka", "Марковский район"],
  ["Saky", "Сакский район"],
  ["Nakhimovskyi", "Нахимовский район"],
  ["Balakavaskyi", "Балаклавский район"],
  ["Popasna", "Попаснянский район"]
]);

const TERRITORY_BY_REGION_ISO = new Map(Object.entries({
  "RU-ALT": "01", "RU-MO": "89", "RU-TUL": "70", "RU-KGN": "37",
  "RU-IN": "26", "RU-KHM": "71", "RU-KIR": "33", "RU-KO": "87",
  "RU-KOS": "34", "RU-KYA": "04", "RU-ZAB": "76", "RU-SVE": "65",
  "RU-VGG": "18", "RU-IRK": "25", "RU-PER": "57", "RU-PSK": "58",
  "RU-ROS": "60", "RU-RYA": "61", "RU-AD": "79", "RU-SAM": "36",
  "RU-KK": "95", "RU-TAM": "68", "RU-TA": "92", "RU-TOM": "69",
  "RU-NIZ": "22", "RU-KR": "86", "RU-ARK": "11", "RU-AST": "12",
  "RU-BEL": "14", "RU-BRY": "15", "RU-BU": "81", "RU-CE": "96",
  "RU-CHE": "75", "RU-CU": "97", "RU-TYU": "71", "RU-SE": "90",
  "RU-PNZ": "56", "RU-AMU": "10", "RU-KB": "83", "RU-KDA": "03",
  "RU-KRS": "38", "RU-LEN": "41", "RU-ME": "88", "RU-MOW": "45",
  "RU-MOS": "46", "RU-MUR": "47", "RU-NEN": "11", "RU-NGR": "49",
  "RU-NVS": "50", "RU-OMS": "52", "RU-ORL": "54", "RU-SPE": "40",
  "RU-SAK": "64", "RU-SA": "98", "RU-SAR": "63", "RU-SMO": "66",
  "RU-STA": "07", "RU-TY": "93", "RU-TVE": "28", "RU-UD": "94",
  "RU-KLU": "29", "RU-LIP": "42", "RU-MAG": "44", "RU-ULY": "73",
  "RU-VLA": "17", "RU-VLG": "19", "RU-YAR": "78", "RU-VOR": "20",
  "RU-YAN": "71", "RU-AL": "84", "RU-IVA": "24", "RU-YEV": "99",
  "RU-KL": "85", "RU-KAM": "30", "RU-KC": "91", "RU-KEM": "32",
  "RU-KHA": "08", "RU-CHU": "77", "RU-DA": "82", "RU-KGD": "27",
  "RU-ORE": "53", "RU-PRI": "05", "RU-BA": "80"
}));

const TERRITORY_BY_REGION_NAME = new Map([
  ["Республика Крым", "35"],
  ["Донецкая Народная Республика", "21"],
  ["Херсонская область", "74"],
  ["Луганская Народная Республика", "43"],
  ["Севастополь", "67"],
  ["Запорожская область", "23"]
]);

export const officialTerritoryForRegion = ({ iso, name }) =>
  TERRITORY_BY_REGION_ISO.get(iso) ?? TERRITORY_BY_REGION_NAME.get(name) ?? null;
