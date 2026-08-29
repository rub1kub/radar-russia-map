import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { supplementalDistrictNames } from "./official-district-names.mjs";

const payload = JSON.parse(readFileSync(
  new URL("../public/data/places.json", import.meta.url),
  "utf8"
));
const districts = JSON.parse(readFileSync(
  new URL("../public/data/districts.json", import.meta.url),
  "utf8"
));
const regions = JSON.parse(readFileSync(
  new URL("../public/data/regions.json", import.meta.url),
  "utf8"
));
const supplementalSource = JSON.parse(readFileSync(
  new URL("../research/data_sources/geoboundaries_UKR_ADM2_simplified.geojson", import.meta.url),
  "utf8"
));
const namesById = new Map(payload.rows.map((row) => [String(row[0]), row[1]]));
const districtById = new Map(districts.features.map((feature) => [
  feature.properties.id,
  feature
]));
const hasNonCyrillicLetter = (value) => [...value].some((character) =>
  /\p{Letter}/u.test(character) && !/\p{Script=Cyrillic}/u.test(character)
);

describe("prepared place names", () => {
  it("не содержит латиницу и украинские буквы в отображаемом имени", () => {
    const invalid = payload.rows.filter((row) =>
      hasNonCyrillicLetter(row[1]) || /[ІіЇїЄєҐґ]/.test(row[1])
    );
    expect(invalid).toEqual([]);
  });

  it.each([
    ["556951", "Ильский"],
    ["500886", "Ртищево"],
    ["483029", "Тихорецк"],
    ["558418", "Грозный"],
    ["13580034", "Правый Берег"],
    ["13607722", "Центральный"],
    ["13561145", "Киевский"],
    ["13607724", "Левобережный"],
    ["13607662", "Каменнобродский"],
    ["699986", "Нижние Серогозы"]
  ])("использует каноническое имя GeoNames %s", (id, expected) => {
    expect(namesById.get(id)).toBe(expected);
  });
});

describe("prepared district names", () => {
  const byId = new Map(districts.features.map((feature) => [
    feature.properties.id,
    feature.properties.name
  ]));

  it("не содержит латиницу, украинские буквы и обрезанные типы", () => {
    for (const name of byId.values()) {
      expect(hasNonCyrillicLetter(name)).toBe(false);
      expect(name).not.toMatch(/[ІіЇїЄєҐґ]/);
      expect(name).not.toMatch(/\b(?:ок|окр|окру|мун)$/i);
      expect(name).not.toMatch(/\b(?:Китй|Регион|Натионал|Дистрикт|Форматион)\b/i);
    }
  });

  it.each([
    ["50074027B22946859849779", "Грозный"],
    ["50074027B93366024646378", "городской округ Горячий Ключ"],
    ["74538382B84610439401970", "Бахчисарайский район"],
    ["74538382B24070697851653", "Артемовский район"],
    ["74538382B3276437105714", "Нахимовский район"]
  ])("содержит каноническое имя района %s", (id, expected) => {
    expect(byId.get(id)).toBe(expected);
  });

  it("сохраняет весь supplemental-набор под закреплёнными русскими именами", () => {
    const selected = supplementalSource.features.filter((feature) =>
      byId.has(feature.properties.shapeID)
    );
    expect(selected).toHaveLength(89);
    for (const feature of selected) {
      const expected = supplementalDistrictNames.get(feature.properties.shapeName);
      if (!expected) continue;
      expect(byId.get(feature.properties.shapeID)).toBe(expected);
      expect(districtById.get(feature.properties.shapeID)?.properties.nameLocked)
        .toBe(true);
    }
  });
});

describe("prepared region names", () => {
  it("содержит только непустые русские отображаемые имена", () => {
    for (const feature of regions.features) {
      const name = feature.properties.name;
      expect(name).toBeTruthy();
      expect(hasNonCyrillicLetter(name)).toBe(false);
      expect(name).not.toMatch(/[ІіЇїЄєҐґ]/);
    }
  });
});

// Справочник строится из полного дампа GeoNames, а не из выборки крупных
// городов. Проверка держит это свойство: когда ETL питался урезанным
// набором, посёлок Ильский (23 тысячи жителей, НПЗ) отсутствовал целиком —
// сообщения о нём молча теряли место и падали на регион источника.
describe("покрытие населённых пунктов", () => {
  const dumpIds = new Set(
    execFileSync("unzip", [
      "-p",
      new URL("../research/data_sources/geonames_RU.zip", import.meta.url).pathname,
      "RU.txt"
    ], { encoding: "utf8", maxBuffer: 1024 * 1024 * 512 })
      .split(/\r?\n/)
      .filter(Boolean)
      .map((line) => line.split("\t"))
      .filter((cols) => cols[6] === "P" && Number(cols[14]) >= 5000)
      .map((cols) => cols[0])
  );

  it("не теряет ни одного населённого пункта от пяти тысяч жителей", () => {
    const present = new Set(payload.rows.map((row) => String(row[0])));
    const missing = [...dumpIds].filter((id) => !present.has(id));
    expect(missing).toEqual([]);
  });

  it("посёлок Ильский на месте с координатами из данных", () => {
    const ilsky = payload.rows.find((row) => String(row[0]) === "556951");
    expect(ilsky?.[1]).toBe("Ильский");
    expect(ilsky?.[3]).toBeCloseTo(44.84222, 4);
    expect(ilsky?.[4]).toBeCloseTo(38.56686, 4);
  });
});
