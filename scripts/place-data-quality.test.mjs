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
