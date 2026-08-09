import { describe, expect, it } from "vitest";
import {
  buildGeoNamesAdminTerritoryMap,
  buildOfficialPlaceRegistry,
  buildRussianLanguageNameMap,
  parseSemicolonCsvRow,
  resolveOfficialPlaceName
} from "./official-place-names.mjs";

const registryCsv = [
  '"03";"643";"155";"051";"0";"2";"пгт Ильский";;;"000";"0";14.06.2013;01.01.2014',
  '"11";"701";"000";"001";"8";"2";"г Архангельск";;;"000";"0";14.06.2013;01.01.2014',
  '"63";"701";"000";"001";"8";"2";"г Саратов";;;"000";"0";14.06.2013;01.01.2014',
  '"65";"752";"000";"001";"2";"2";"г Новоуральск";;;"000";"0";14.06.2013;01.01.2014',
  '"65";"751";"000";"001";"2";"2";"п Верх-Нейвинский";;;"000";"0";14.06.2013;01.01.2014'
].join("\n");

const geonamesLine = ({ id, name, ascii, alternates, admin }) => {
  const columns = Array(19).fill("");
  Object.assign(columns, {
    0: id, 1: name, 2: ascii, 3: alternates,
    4: "44.84222", 5: "38.56686", 6: "P", 7: "PPL",
    8: "RU", 10: admin, 14: "22970"
  });
  return columns.join("\t");
};

describe("official place names", () => {
  it("разбирает кавычки и точки с запятой в CSV ОКТМО", () => {
    expect(parseSemicolonCsvRow('"03";"2";"п свх ""Юбилейный; Север""";;')).toEqual([
      "03", "2", 'п свх "Юбилейный; Север"', "", ""
    ]);
  });

  it("сопоставляет GeoNames admin1 с субъектом ОКТМО по официальным именам", () => {
    const registry = buildOfficialPlaceRegistry(registryCsv);
    const mapping = buildGeoNamesAdminTerritoryMap(geonamesLine({
      id: "556951", name: "Il’skiy", ascii: "Il'skiy",
      alternates: "Ilskij,Ильский", admin: "38"
    }), registry);

    expect(mapping.get("38")).toBe("03");
  });

  it("читает актуальные русские имена с признаками GeoNames", () => {
    const names = buildRussianLanguageNameMap([
      "geoname_id\tname\tpreferred\thistoric\tfrom\tto",
      "558418\tГрозный\t1\t0\t\t",
      "713174\tАртемовск\t0\t1\t\t"
    ].join("\n"));

    expect(names.get("558418")).toEqual([{
      name: "Грозный", preferred: true, historic: false, from: "", to: ""
    }]);
    expect(names.get("713174")?.[0].historic).toBe(true);
  });

  it.each([
    ["Илский", "Il'skiy", "Ilskij,Ильский", "38", "Ильский"],
    ["Архангелск", "Arkhangel'sk", "Архангельск", "06", "Архангельск"],
    ["Саратовъ", "Saratov", "Саратов", "67", "Саратов"]
  ])("исправляет %s по официальному реестру", (currentName, asciiName, alternates, adminCode, expected) => {
    const registry = buildOfficialPlaceRegistry(registryCsv);
    const adminTerritories = new Map([["38", "03"], ["06", "11"], ["67", "63"]]);
    expect(resolveOfficialPlaceName({
      currentName,
      sourceName: asciiName,
      asciiName,
      alternates,
      adminCode,
      registry,
      adminTerritories
    })).toBe(expected);
  });

  it("не принимает исторический вариант, хуже соответствующий исходной точке", () => {
    const registry = buildOfficialPlaceRegistry(registryCsv);
    expect(resolveOfficialPlaceName({
      currentName: "Новоуральск",
      sourceName: "Novoural'sk",
      asciiName: "Novoural'sk",
      alternates: "Верх-Нейвинский",
      adminCode: "45",
      registry,
      adminTerritories: new Map([["45", "65"]])
    })).toBe("Новоуральск");
  });

  it("принимает preferred-имя, подтверждённое ОКТМО в том же субъекте", () => {
    const registry = buildOfficialPlaceRegistry([
      '"96";"701";"000";"001";"4";"2";"г Грозный";;;;;'
    ].join("\n"));
    expect(resolveOfficialPlaceName({
      currentName: "Грозны",
      sourceName: "Grozny",
      asciiName: "Grozny",
      alternates: "Грозны",
      adminCode: "12",
      registry,
      adminTerritories: new Map([["12", "96"]]),
      languageNames: [{
        name: "Грозный", preferred: true, historic: false, from: "", to: ""
      }]
    })).toBe("Грозный");
  });

  it("для supplemental берёт текущее русское имя, но не историческое", () => {
    const registry = buildOfficialPlaceRegistry([
      '"74";"701";"000";"001";"4";"2";"пгт Нижние Серогозы";;;;;',
      '"21";"701";"000";"002";"4";"2";"г Артемовск";;;;;'
    ].join("\n"));
    const context = {
      registry,
      adminTerritories: new Map([["08", "74"], ["05", "21"]]),
      preferCurrentLanguageName: true
    };

    expect(resolveOfficialPlaceName({
      ...context,
      currentName: "Ныжни Сирагозы",
      sourceName: "Nyzhni Sirohozy",
      asciiName: "Nyzhni Sirohozy",
      alternates: "",
      adminCode: "08",
      languageNames: [{
        name: "Нижние Серогозы", preferred: false, historic: false, from: "", to: ""
      }]
    })).toBe("Нижние Серогозы");
    expect(resolveOfficialPlaceName({
      ...context,
      currentName: "Бахмут",
      sourceName: "Bakhmut",
      asciiName: "Bakhmut",
      alternates: "",
      adminCode: "05",
      languageNames: [{
        name: "Артемовск", preferred: false, historic: true, from: "", to: ""
      }]
    })).toBe("Бахмут");
  });
});
