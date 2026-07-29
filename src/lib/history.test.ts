import { describe, expect, it } from "vitest";
import { activeAt, buildSlots, eventsAt, zoneCountsAt } from "./history";
import type { RadarEvent } from "./api";

function event(overrides: Partial<RadarEvent> = {}): RadarEvent {
  return {
    id: "e1",
    first_seen_at: "2026-07-27T10:00:00+00:00",
    last_seen_at: "2026-07-27T10:10:00+00:00",
    resolved_at: null,
    status: "active",
    signal_type: "danger",
    threat_type: "uav",
    severity: 5,
    confidence: 0.55,
    source_count: 1,
    zone_id: "azovskiy_rayon",
    zone_path: ["azovskiy_rayon", "rostov_oblast"],
    place_name: "Азовский район",
    zone_level: "district",
    lat: 47.1,
    lon: 39.4,
    accuracy_m: 12000,
    target_count: null,
    ...overrides
  };
}

const at = (iso: string) => new Date(iso).getTime();

describe("activeAt", () => {
  it("не активно до начала", () => {
    expect(activeAt(event(), at("2026-07-27T09:59:00Z"))).toBe(false);
  });

  it("активно в момент между началом и последним сообщением", () => {
    expect(activeAt(event(), at("2026-07-27T10:05:00Z"))).toBe(true);
  });

  it("остаётся затухающим три часа после последнего сообщения", () => {
    expect(activeAt(event(), at("2026-07-27T12:00:00Z"))).toBe(true);
    expect(activeAt(event(), at("2026-07-27T13:30:00Z"))).toBe(false);
  });

  it("отбой закрывает событие немедленно", () => {
    const closed = event({ resolved_at: "2026-07-27T10:20:00+00:00" });
    expect(activeAt(closed, at("2026-07-27T10:15:00Z"))).toBe(true);
    expect(activeAt(closed, at("2026-07-27T10:25:00Z"))).toBe(false);
  });
});

describe("buildSlots", () => {
  it("режет окно с шагом 15 минут", () => {
    const slots = buildSlots("2026-07-27T10:00:00Z", "2026-07-27T11:00:00Z");
    expect(slots.length).toBe(5);
  });

  it("пустое окно даёт минимум срезов", () => {
    expect(buildSlots("2026-07-27T10:00:00Z", "2026-07-27T10:00:00Z").length).toBeLessThanOrEqual(1);
  });
});

describe("zoneCountsAt", () => {
  it("поднимает событие по всей цепочке родителей", () => {
    const counts = zoneCountsAt([event()], "2026-07-27T10:05:00Z", {});
    expect(counts.azovskiy_rayon.active).toBe(1);
    expect(counts.rostov_oblast.active).toBe(1);
  });

  it("складывает несколько событий в одну зону", () => {
    const counts = zoneCountsAt(
      [event(), event({ id: "e2", severity: 8 })],
      "2026-07-27T10:05:00Z",
      {}
    );
    expect(counts.rostov_oblast.active).toBe(2);
    expect(counts.rostov_oblast.max_severity).toBe(8);
  });

  it("на пустой момент не даёт зон", () => {
    expect(Object.keys(zoneCountsAt([event()], "2026-07-27T09:00:00Z", {})).length).toBe(0);
  });

  it("подтягивает source_id из справочника счётчиков", () => {
    const counts = zoneCountsAt([event()], "2026-07-27T10:05:00Z", {
      azovskiy_rayon: {
        level: "district",
        source_id: "POLY-42",
        name: "Азовский район"
      }
    });
    expect(counts.azovskiy_rayon.source_id).toBe("POLY-42");
  });
});

describe("eventsAt", () => {
  it("отбирает только активные на момент", () => {
    const early = event({ id: "early", first_seen_at: "2026-07-27T09:00:00+00:00", last_seen_at: "2026-07-27T09:05:00+00:00" });
    expect(eventsAt([event(), early], "2026-07-27T10:05:00Z").map((e) => e.id)).toEqual(["e1", "early"]);
    expect(eventsAt([event(), early], "2026-07-27T09:02:00Z").map((e) => e.id)).toEqual(["early"]);
  });
});

describe("цвет зоны в истории", () => {
  const at = "2026-07-28T12:00:00Z";
  const event = (over: Partial<RadarEvent>): RadarEvent =>
    ({
      id: over.id ?? "e",
      zone_id: "kursk_district",
      zone_path: ["kursk_district", "kurskaya_oblast"],
      severity: 5,
      signal_type: "danger",
      threat_type: "uav",
      first_seen_at: "2026-07-28T09:00:00Z",
      last_seen_at: "2026-07-28T12:00:00Z",
      resolved_at: null,
      status: "active",
      source_count: 1,
      confidence: 0.5,
      lat: 51,
      lon: 36,
      accuracy_m: 12000,
      direction_deg: null,
      target_count: null,
      place_name: "Курск",
      zone_level: "district",
      ...over
    }) as RadarEvent;

  it("свежая тревога перебивает давнюю фиксацию", () => {
    const counts = zoneCountsAt(
      [
        event({ id: "old", severity: 9, signal_type: "detection", last_seen_at: "2026-07-28T10:00:00Z" }),
        event({ id: "new", severity: 7, signal_type: "alarm", last_seen_at: "2026-07-28T11:59:00Z" })
      ],
      at,
      {}
    );
    // Иначе район горел бы красным из-за того, что было и прошло.
    expect(counts.kursk_district.severity).toBe(7);
    expect(counts.kursk_district.max_severity).toBe(9);
  });

  it("когда всё старое, цвет прежний, но приглушённый", () => {
    const counts = zoneCountsAt(
      [event({ severity: 9, signal_type: "detection", last_seen_at: "2026-07-28T10:00:00Z" })],
      at,
      {}
    );
    expect(counts.kursk_district.severity).toBe(9);
    // Два часа без нового сообщения — район давно догорел до минимума.
    expect(counts.kursk_district.fade).toBeCloseTo(0.12, 2);
  });

  it("собственные события зоны считаются отдельно от унаследованных", () => {
    const counts = zoneCountsAt([event({ severity: 9, signal_type: "detection" })], at, {});
    expect(counts.kursk_district.own).toBe(1);
    // У области событие только унаследованное — своего нет.
    expect(counts.kurskaya_oblast.own).toBe(0);
    expect(counts.kurskaya_oblast.active).toBe(1);
  });
});
