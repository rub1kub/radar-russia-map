import { describe, expect, it } from "vitest";
import {
  fadeWindow,
  freshness,
  REGION_NEAR_WASH,
  regionWeight,
  ZONE_FADE_MS,
  zoneFillAlpha
} from "./paint";

const NOW = new Date("2026-07-28T12:00:00Z").getTime();
const ago = (ms: number) => new Date(NOW - ms).toISOString();

describe("freshness", () => {
  it("свежее сообщение горит в полную силу", () => {
    expect(freshness(ago(0), NOW)).toBe(1);
  });

  it("район гаснет за полчаса: борт его за это время пересекает", () => {
    // Украинские дальнобойные — Хорнет, Бобр, Дартс, Лютый — идут около
    // 150 км/ч, район поперёк 73 км.
    expect(freshness(ago(15 * 60 * 1000), NOW, "district", "uav")).toBeCloseTo(0.29, 2);
    expect(freshness(ago(30 * 60 * 1000), NOW, "district", "uav")).toBe(0.12);
  });

  it("регион держится дольше района: он вшестеро шире", () => {
    expect(freshness(ago(30 * 60 * 1000), NOW, "region", "uav")).toBeGreaterThan(
      freshness(ago(30 * 60 * 1000), NOW, "district", "uav")
    );
  });

  it("ракета покидает зону быстрее дрона", () => {
    expect(freshness(ago(6 * 60 * 1000), NOW, "district", "rocket")).toBeLessThan(
      freshness(ago(6 * 60 * 1000), NOW, "district", "uav")
    );
  });

  it("срок не короче задержки самого сообщения", () => {
    expect(fadeWindow("place", "rocket")).toBe(8 * 60 * 1000);
  });

  it("первые минуты значат больше поздних", () => {
    const early = freshness(ago(5 * 60 * 1000), NOW) - freshness(ago(35 * 60 * 1000), NOW);
    const late =
      freshness(ago(125 * 60 * 1000), NOW) - freshness(ago(155 * 60 * 1000), NOW);
    expect(early).toBeGreaterThan(late);
  });

  it("старое не гаснет совсем: событие ещё не закрыто", () => {
    expect(freshness(ago(ZONE_FADE_MS * 5), NOW)).toBe(0.12);
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

describe("область вблизи — фон, а не сигнал", () => {
  it("собственная заливка области приглушается заметно", () => {
    // Погасший район выглядел таким же красным, как горящий рядом: видно
    // было не его, а область поверх.
    expect(REGION_NEAR_WASH).toBeLessThan(0.5);
    expect(REGION_NEAR_WASH).toBeGreaterThan(0.15);
  });

  it("район в полную силу перебивает фон области", () => {
    const district = zoneFillAlpha(4) * freshness(ago(2 * 60 * 1000), NOW, "district", "uav");
    const regionWash =
      zoneFillAlpha(8) * freshness(ago(60 * 60 * 1000), NOW, "region", "uav") * REGION_NEAR_WASH;
    expect(district).toBeGreaterThan(regionWash);
  });
});
