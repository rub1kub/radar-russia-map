// @vitest-environment jsdom
// Хранилище браузера здесь настоящее: правило «адрес важнее памяти»
// проверяется вместе с записью, а не на заглушке.
import { beforeEach, describe, expect, it } from "vitest";
import { loadHomeRegion, pickStartRegion, rememberHomeRegion } from "./homeRegion";

describe("выбор стартового региона", () => {
  it("адрес важнее памяти", () => {
    expect(pickStartRegion("kurskaya-oblast", "tulskaya_oblast")).toBe(
      "kurskaya_oblast"
    );
  });

  it("без параметра открывает запомненный", () => {
    expect(pickStartRegion(null, "tulskaya_oblast")).toBe("tulskaya_oblast");
  });

  it("без параметра и без памяти — обзор страны", () => {
    expect(pickStartRegion(null, null)).toBeNull();
    expect(pickStartRegion("", "")).toBeNull();
  });

  it("слаг адреса переводится в зону справочника", () => {
    expect(pickStartRegion("respublika-severnaya-osetiya", null)).toBe(
      "respublika_severnaya_osetiya"
    );
  });
});

describe("память о своём регионе", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("запоминает и отдаёт обратно", () => {
    rememberHomeRegion("belgorodskaya_oblast");
    expect(loadHomeRegion()).toBe("belgorodskaya_oblast");
  });

  it("снятое выделение забывает регион", () => {
    rememberHomeRegion("belgorodskaya_oblast");
    rememberHomeRegion(null);
    expect(loadHomeRegion()).toBeNull();
  });
});
