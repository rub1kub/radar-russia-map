import { describe, expect, it } from "vitest";
import {
  buildOfficialDistrictRegistry,
  officialTerritoryForRegion,
  resolveOfficialDistrictName,
  supplementalDistrictNames
} from "./official-district-names.mjs";

const row = (territory, code, name, center = "") =>
  `"${territory}";"${code}";"000";"000";"0";"1";"${name}";"${center}";;"000";"0";;`;

const registry = buildOfficialDistrictRegistry([
  row("96", "701", "город Грозный", "г Грозный"),
  row("03", "504", "Муниципальный округ город Горячий Ключ", "г Горячий Ключ"),
  row("58", "617", "Опочецкий муниципальный район", "г Опочка"),
  row("36", "701", "город Тольятти", "г Тольятти"),
  row("36", "606", "Ставропольский муниципальный район", "г Тольятти"),
  row("03", "501", "Муниципальный округ город-курорт Анапа", "г Анапа"),
  row("03", "720", "город-герой Новороссийск", "г Новороссийск"),
  row("11", "501", "Муниципальный округ Новая Земля", "п Белушья Губа"),
  row("22", "511", "Муниципальный округ город Бор", "г Бор"),
  row("63", "606", "Дергачевский муниципальный район", "рп Дергачи"),
  row("63", "701", "город Саратов", "г Саратов"),
  row("98", "606", "Амгинский муниципальный район", "с Амга"),
  '"36";"600";"000";"000";"0";"1";"Муниципальные районы Самарской области";;;;;;'
].join("\n"));

describe("official district names", () => {
  it("не принимает групповые строки ОКТМО за районы", () => {
    expect(registry.candidates).toHaveLength(12);
  });

  it.each([
    ["Grozny", "Грозны", "Грозный"],
    ["городской округ Горячий Кл", "городской округ Горячий Кл", "городской округ Горячий Ключ"],
    ["Opochetsky District", "Опочетский район", "Опочецкий район"]
  ])("исправляет %s по официальному названию", (sourceName, currentName, expected) => {
    expect(resolveOfficialDistrictName({ sourceName, currentName, registry })).toBe(expected);
  });

  it("отличает город от района с тем же административным центром", () => {
    expect(resolveOfficialDistrictName({
      sourceName: "Tolyatti",
      currentName: "Тольятти",
      registry
    })).toBe("Тольятти");
  });

  it("доверяет исходному полигону, а не ошибочному соседнему имени", () => {
    expect(resolveOfficialDistrictName({
      sourceName: "Anapa Urban Okrug",
      currentName: "городской округ Новороссий",
      registry,
      territory: "03"
    })).toBe("городской округ Анапа");
  });

  it("не подменяет название округа его административным центром", () => {
    expect(resolveOfficialDistrictName({
      sourceName: "городской округ Новая Земл",
      currentName: "городской округ Новая Земл",
      registry,
      territory: "11"
    })).toBe("городской округ Новая Земля");
  });

  it("сохраняет корректную букву ё", () => {
    expect(resolveOfficialDistrictName({
      sourceName: "Dergachyovsky District",
      currentName: "Дергачёвский район",
      registry,
      territory: "63"
    })).toBe("Дергачёвский район");
  });

  it("сохраняет региональный тип улус", () => {
    expect(resolveOfficialDistrictName({
      sourceName: "Amginsky Ulus",
      currentName: "Амгинский Перевоз",
      registry,
      territory: "98"
    })).toBe("Амгинский улус");
  });

  it("не превращает бывший район в одноимённый город по слабому совпадению", () => {
    expect(resolveOfficialDistrictName({
      sourceName: "Saratovsky District",
      currentName: "Саратовский Второй",
      fallbackName: "Саратовский район",
      registry,
      territory: "63"
    })).toBe("Саратовский район");
  });

  it("не подменяет слабое совпадение чужим районом", () => {
    expect(resolveOfficialDistrictName({
      sourceName: "Completely Unknown",
      currentName: "Неизвестный район",
      registry
    })).toBe("Неизвестный район");
  });

  it("покрывает все supplemental-полигоны русскими именами", () => {
    expect(supplementalDistrictNames.size).toBe(89);
    expect(supplementalDistrictNames.get("Bakhchysarai")).toBe("Бахчисарайский район");
    expect(supplementalDistrictNames.get("Bakhmut")).toBe("Артемовский район");
    expect(supplementalDistrictNames.get("Nakhimovskyi")).toBe("Нахимовский район");
  });

  it("сопоставляет субъекты с кодами ОКТМО", () => {
    expect(officialTerritoryForRegion({ iso: "RU-KDA", name: "Краснодарский край" })).toBe("03");
    expect(officialTerritoryForRegion({ iso: null, name: "Донецкая Народная Республика" })).toBe("21");
    expect(officialTerritoryForRegion({ iso: null, name: "Азовское море" })).toBeNull();
  });
});
