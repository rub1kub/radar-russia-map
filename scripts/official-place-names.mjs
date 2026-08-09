const RUSSIAN_PLACE_NAME = /^[А-Яа-яЁё0-9 .'-]+$/;
const PLACE_TYPE_PREFIX = /^(?:город|г|пгт|рп|дп|кп|с|п|д|х|ст-ца|ст|сл|аул|нп|тер|аал|улус|рзд|платф)\.?\s+/i;

// В GeoNames Москва и Санкт-Петербург имеют отдельные admin1-коды, но
// голосование по населённым пунктам у федеральных городов слишком слабое:
// вокруг них неизбежно побеждают одноимённые области.
const ADMIN_TERRITORY_OVERRIDES = new Map([
  ["48", "45"], // Москва
  ["66", "40"]  // Санкт-Петербург
]);

const ruToLatin = {
  а: "a", б: "b", в: "v", г: "g", д: "d", е: "e", ё: "e",
  ж: "zh", з: "z", и: "i", й: "y", к: "k", л: "l", м: "m",
  н: "n", о: "o", п: "p", р: "r", с: "s", т: "t", у: "u",
  ф: "f", х: "h", ц: "ts", ч: "ch", ш: "sh", щ: "sch",
  ъ: "", ы: "y", ь: "", э: "e", ю: "yu", я: "ya"
};

const normalizeLatin = (value) =>
  String(value || "")
    .normalize("NFC")
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

export const parseSemicolonCsvRow = (line) => {
  const fields = [];
  let field = "";
  let quoted = false;

  for (let index = 0; index < line.length; index += 1) {
    const char = line[index];
    if (quoted) {
      if (char === '"' && line[index + 1] === '"') {
        field += '"';
        index += 1;
      } else if (char === '"') {
        quoted = false;
      } else {
        field += char;
      }
    } else if (char === '"' && field.length === 0) {
      quoted = true;
    } else if (char === ";") {
      fields.push(field);
      field = "";
    } else {
      field += char;
    }
  }

  if (quoted) throw new Error("Незакрытая кавычка в строке ОКТМО");
  fields.push(field);
  return fields;
};

const cleanOfficialPlaceName = (value) =>
  String(value || "")
    .normalize("NFC")
    .replace(/\s+/g, " ")
    .trim()
    .replace(PLACE_TYPE_PREFIX, "")
    .trim();

const normalizeCyrillic = (value) =>
  cleanOfficialPlaceName(value)
    .toLowerCase()
    .replace(/ё/g, "е")
    .replace(/[^а-я0-9]+/g, "");

export const russianPlaceCandidates = (name, alternates) => {
  const values = [name, ...String(alternates || "").split(",")];
  return [...new Set(values
    .map((value) => String(value || "").normalize("NFC").trim())
    .filter((value) => value.length > 1 && value.length <= 80 && RUSSIAN_PLACE_NAME.test(value)))];
};

export const buildOfficialPlaceRegistry = (csvRaw) => {
  const namesByTerritory = new Map();
  const territoriesByName = new Map();

  for (const line of String(csvRaw || "").split(/\r?\n/)) {
    if (!line) continue;
    const row = parseSemicolonCsvRow(line);
    const territory = row[0];
    if (!/^\d{2}$/.test(territory) || row.length < 8) continue;

    for (const rawName of [row[6], row[7]]) {
      const name = cleanOfficialPlaceName(rawName);
      if (name.length <= 1 || name.length > 80 || !RUSSIAN_PLACE_NAME.test(name)) continue;

      if (!namesByTerritory.has(territory)) namesByTerritory.set(territory, new Set());
      namesByTerritory.get(territory).add(name);
      if (!territoriesByName.has(name)) territoriesByName.set(name, new Set());
      territoriesByName.get(name).add(territory);
    }
  }

  return { namesByTerritory, territoriesByName };
};

export const buildRussianLanguageNameMap = (tsvRaw) => {
  const namesById = new Map();
  for (const line of String(tsvRaw || "").split(/\r?\n/)) {
    if (!line || line.startsWith("geoname_id\t")) continue;
    const [id, name, preferred, historic, from, to] = line.split("\t");
    if (!/^\d+$/.test(id) || !name) continue;
    if (!namesById.has(id)) namesById.set(id, []);
    namesById.get(id).push({
      name,
      preferred: preferred === "1",
      historic: historic === "1",
      from: from || "",
      to: to || ""
    });
  }
  return namesById;
};

const addVote = (votes, adminCode, territory) => {
  if (!adminCode || !territory) return;
  if (!votes.has(adminCode)) votes.set(adminCode, new Map());
  const counter = votes.get(adminCode);
  counter.set(territory, (counter.get(territory) ?? 0) + 1);
};

export const buildGeoNamesAdminTerritoryMap = (geonamesRaw, registry) => {
  const votes = new Map();
  for (const line of String(geonamesRaw || "").split(/\r?\n/)) {
    if (!line) continue;
    const columns = line.split("\t");
    if (columns.length < 19 || columns[6] !== "P") continue;

    for (const name of russianPlaceCandidates(columns[1], columns[3])) {
      const territories = registry.territoriesByName.get(name);
      if (territories?.size === 1) addVote(votes, columns[10], territories.values().next().value);
    }
  }

  const result = new Map();
  for (const [adminCode, counter] of votes) {
    const winner = [...counter.entries()].sort((left, right) => right[1] - left[1])[0];
    if (winner) result.set(adminCode, winner[0]);
  }
  for (const [adminCode, territory] of ADMIN_TERRITORY_OVERRIDES) {
    result.set(adminCode, territory);
  }
  return result;
};

export const resolveOfficialPlaceName = ({
  currentName,
  sourceName,
  asciiName,
  alternates,
  adminCode,
  registry,
  adminTerritories,
  languageNames = [],
  preferCurrentLanguageName = false
}) => {
  const territory = adminTerritories.get(adminCode);
  const officialNames = registry.namesByTerritory.get(territory);
  if (!officialNames) return currentName;

  // Если уже выбранное имя есть в официальном реестре своего субъекта,
  // альтернативы не должны подменять его историческим или соседним.
  if (officialNames.has(currentName)) return currentName;

  const target = normalizeLatin(asciiName || sourceName || currentName);
  const currentLanguageNames = languageNames
    .filter((item) => !item.historic && !item.to && officialNames.has(item.name))
    .map((item, index) => ({
      ...item,
      index,
      score: levenshtein(normalizeLatin(item.name), target)
    }))
    .sort((left, right) =>
      Number(right.preferred) - Number(left.preferred)
      || left.score - right.score
      || left.index - right.index
    );

  // GeoNames пометил имя русским и текущим, ОКТМО подтвердил его в том же
  // субъекте. Для supplemental-территорий это надёжнее механической замены
  // украинских окончаний; в основной части страны безусловно берём только
  // явно preferred-вариант.
  const trustedLanguageName = currentLanguageNames.find((item) =>
    item.preferred || preferCurrentLanguageName
  );
  if (trustedLanguageName) return trustedLanguageName.name;

  const candidates = [...new Set([
    currentName,
    ...russianPlaceCandidates(sourceName, alternates),
    ...currentLanguageNames.map((item) => item.name)
  ].filter(Boolean))];
  const currentScore = levenshtein(normalizeLatin(currentName), target);
  const official = candidates
    .map((name, index) => ({ name, index, score: levenshtein(normalizeLatin(name), target) }))
    .filter((candidate) => officialNames.has(candidate.name))
    .sort((left, right) => left.score - right.score || left.index - right.index);

  if (!official[0]) return currentName;

  // ОКТМО отвечает за написание, но не за идентичность точки. Историческое
  // имя из alternateNames может тоже существовать в реестре; оно принимается
  // лишь когда соответствует GeoNames не хуже уже выбранного варианта либо
  // отличается от него как короткая орфографическая ошибка.
  const currentCyrillic = normalizeCyrillic(currentName);
  const officialCyrillic = normalizeCyrillic(official[0].name);
  const typoLimit = Math.min(2, Math.max(1, Math.floor(currentCyrillic.length / 8)));
  const isNearTypo = levenshtein(currentCyrillic, officialCyrillic) <= typoLimit
    && official[0].score <= currentScore + 2;
  return official[0].score <= currentScore || isNearTypo
    ? official[0].name
    : currentName;
};
