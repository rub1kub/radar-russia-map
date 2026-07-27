import { describe, expect, it } from "vitest";
import { freshness, regionWeight, ZONE_FADE_MS, zoneFillAlpha } from "./paint";

const NOW = new Date("2026-07-28T12:00:00Z").getTime();
const ago = (ms: number) => new Date(NOW - ms).toISOString();

describe("freshness", () => {
  it("свежее сообщение горит в полную силу", () => {
    expect(freshness(ago(0), NOW)).toBe(1);
  });

  it("час давности примерно вдвое тусклее", () => {
    expect(freshness(ago(60 * 60 * 1000), NOW)).toBeCloseTo(0.667, 2);
  });

  it("старое не гаснет совсем: событие ещё не закрыто", () => {
    expect(freshness(ago(ZONE_FADE_MS * 5), NOW)).toBe(0.25);
  });

  it("без отметки времени не выцветает", () => {
    expect(freshness(undefined, NOW)).toBe(1);
  });
});

describe("regionWeight", () => {
  it("собственное оповещение по области красит в полную силу", () => {
    expect(regionWeight(1, 1)).toBe(1);
  });

  it("одна фиксация в одном районе почти не красит субъект", () => {
    // Ради этого правило и появилось: весь край краснел из-за одного города.
    expect(regionWeight(0, 1)).toBeLessThan(0.3);
  });

  it("чем больше зон горит, тем гуще заливка", () => {
    expect(regionWeight(0, 10)).toBeGreaterThan(regionWeight(0, 5));
    expect(regionWeight(0, 5)).toBeGreaterThan(regionWeight(0, 2));
  });
});

describe("zoneFillAlpha", () => {
  it("растёт с числом событий", () => {
    expect(zoneFillAlpha(6)).toBeGreaterThan(zoneFillAlpha(3));
    expect(zoneFillAlpha(3)).toBeGreaterThan(zoneFillAlpha(1));
  });
});
