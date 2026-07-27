import { describe, expect, it } from "vitest";
import {
  durationMinutes,
  formatAge,
  formatDuration,
  formatMoment,
  plural,
  severityColor,
  signalLabel,
  threatLabel
} from "./format";

describe("plural", () => {
  it("склоняет по русским правилам", () => {
    expect(plural(1, "сообщение", "сообщения", "сообщений")).toBe("сообщение");
    expect(plural(2, "сообщение", "сообщения", "сообщений")).toBe("сообщения");
    expect(plural(5, "сообщение", "сообщения", "сообщений")).toBe("сообщений");
  });

  it("правильно обрабатывает подводные 11-14", () => {
    expect(plural(11, "источник", "источника", "источников")).toBe("источников");
    expect(plural(12, "источник", "источника", "источников")).toBe("источников");
    expect(plural(14, "источник", "источника", "источников")).toBe("источников");
    expect(plural(21, "источник", "источника", "источников")).toBe("источник");
    expect(plural(112, "источник", "источника", "источников")).toBe("источников");
    expect(plural(122, "источник", "источника", "источников")).toBe("источника");
  });

  it("не падает на нуле", () => {
    expect(plural(0, "зона", "зоны", "зон")).toBe("зон");
  });
});

describe("formatMoment", () => {
  // Время в API — UTC, показывать надо московское.
  it("переводит UTC в московское", () => {
    expect(formatMoment("2026-07-27T10:57:00+00:00", "2026-07-27T11:00:00+00:00")).toBe("13:57");
  });

  it("добавляет дату, если это не сегодня", () => {
    const result = formatMoment("2026-07-26T10:00:00+00:00", "2026-07-27T11:00:00+00:00");
    expect(result).toContain("26.07");
    expect(result).toContain("13:00");
  });

  it("около полуночи по Москве дата берётся московская, а не UTC", () => {
    // 22:30 UTC = 01:30 МСК следующих суток.
    const result = formatMoment("2026-07-26T22:30:00+00:00", "2026-07-27T00:10:00+00:00");
    expect(result).toBe("01:30");
  });
});

describe("formatDuration", () => {
  it("считает минуты", () => {
    expect(formatDuration("2026-07-27T10:00:00Z", "2026-07-27T10:05:00Z")).toBe("5 минут");
    expect(formatDuration("2026-07-27T10:00:00Z", "2026-07-27T10:01:00Z")).toBe("1 минуту");
    expect(formatDuration("2026-07-27T10:00:00Z", "2026-07-27T10:02:00Z")).toBe("2 минуты");
  });

  it("переходит на часы", () => {
    expect(formatDuration("2026-07-27T08:00:00Z", "2026-07-27T10:00:00Z")).toBe("2 часа");
    expect(formatDuration("2026-07-27T08:00:00Z", "2026-07-27T10:10:00Z")).toBe("2 часа 10 мин");
  });

  it("не даёт отрицательной длительности", () => {
    expect(formatDuration("2026-07-27T11:00:00Z", "2026-07-27T10:00:00Z")).toBe("только что");
  });
});

describe("formatAge", () => {
  it("минуты и часы", () => {
    expect(formatAge(300)).toBe("5 минут");
    expect(formatAge(7200)).toBe("2 часа");
  });
});

describe("severityColor", () => {
  it("разводит уровни по цветам", () => {
    const high = severityColor(9, 1);
    const mid = severityColor(7, 1);
    const low = severityColor(4, 1);
    const other = severityColor(2, 1);
    expect(new Set([high, mid, low, other]).size).toBe(4);
  });

  it("не совпадает с цветом дорог подложки", () => {
    // Прежняя палитра давала rgba(228,178,62) — почти дословно цвет трассы.
    expect(severityColor(4, 1)).not.toBe("rgba(228, 178, 62, 1)");
  });

  it("прокидывает прозрачность", () => {
    expect(severityColor(9, 0.5)).toContain("0.5");
  });
});

describe("подписи", () => {
  it("переводят известные коды", () => {
    expect(signalLabel("allclear")).toBe("Отбой");
    expect(threatLabel("uav")).toBe("БПЛА");
  });

  it("возвращают исходный код для неизвестного", () => {
    expect(signalLabel("teleport")).toBe("teleport");
  });
});

describe("durationMinutes", () => {
  it("событие в один момент длительности не имеет", () => {
    expect(durationMinutes("2026-07-27T10:00:00Z", "2026-07-27T10:00:20Z")).toBe(0);
  });

  it("считает минуты между первым и последним сообщением", () => {
    expect(durationMinutes("2026-07-27T10:00:00Z", "2026-07-27T10:47:00Z")).toBe(47);
  });
});
