import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const payload = JSON.parse(readFileSync(
  new URL("../public/data/places.json", import.meta.url),
  "utf8"
));
const namesById = new Map(payload.rows.map((row) => [String(row[0]), row[1]]));

describe("prepared place names", () => {
  it("не содержит латиницу и украинские буквы в отображаемом имени", () => {
    const invalid = payload.rows.filter((row) => /[A-Za-zІіЇїЄєҐґ]/.test(row[1]));
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
