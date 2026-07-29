import { describe, expect, it } from "vitest";
import { inferTrails, trailVisibleAt, TRAIL_TTL_MS } from "./trails";
import type { RadarEvent } from "./api";

const point = (
  id: string,
  lat: number,
  lon: number,
  minute: number,
  over: Partial<RadarEvent> = {}
): RadarEvent =>
  ({
    id,
    zone_id: id,
    zone_path: [id],
    severity: 8,
    signal_type: "detection",
    threat_type: "uav",
    first_seen_at: `2026-07-28T21:${String(minute).padStart(2, "0")}:00Z`,
    last_seen_at: `2026-07-28T21:${String(minute).padStart(2, "0")}:00Z`,
    resolved_at: null,
    status: "active",
    source_count: 1,
    confidence: 0.6,
    lat,
    lon,
    accuracy_m: 12000,
    direction_deg: null,
    target_count: null,
    place_name: id,
    zone_level: "district",
    ...over
  }) as RadarEvent;

describe("inferTrails", () => {
  it("единственное правдоподобное продолжение даёт звено", () => {
    // ~33 км за 10 минут: укладывается в конверт скорости борта.
    const trails = inferTrails([point("a", 47, 39, 0), point("b", 47.3, 39, 10)]);
    expect(trails).toHaveLength(1);
    expect(trails[0].from).toEqual([47, 39]);
    expect(trails[0].to).toEqual([47.3, 39]);
  });

  it("два кандидата — ни одного звена: склейка в налёт была бы враньём", () => {
    const trails = inferTrails([
      point("a", 47, 39, 0),
      point("b", 47.3, 39, 10),
      point("c", 46.7, 39, 10)
    ]);
    expect(trails.filter((trail) => trail.from[0] === 47)).toHaveLength(0);
  });

  it("слишком далеко для прошедшего времени — не звено", () => {
    // 220 км за 10 минут не пролетает даже реактивный дрон.
    expect(inferTrails([point("a", 47, 39, 0), point("b", 49, 39, 10)])).toHaveLength(0);
  });

  it("ракеты не сцепляются: время публикаций о пути не говорит", () => {
    const rocket = { threat_type: "rocket" } as Partial<RadarEvent>;
    expect(
      inferTrails([point("a", 47, 39, 0, rocket), point("b", 47.3, 39, 10, rocket)])
    ).toHaveLength(0);
  });
});

describe("trailVisibleAt", () => {
  const trail: import("./trails").Trail = {
    from: [47, 39],
    to: [47.3, 39],
    at: "2026-07-28T21:10:00Z",
    severity: 8
  };

  it("виден после позднего конца и до конца часа", () => {
    const ended = new Date(trail.at).getTime();
    expect(trailVisibleAt(trail, ended + 10 * 60 * 1000)).toBe(true);
    expect(trailVisibleAt(trail, ended - 60 * 1000)).toBe(false);
    expect(trailVisibleAt(trail, ended + TRAIL_TTL_MS + 1)).toBe(false);
  });
});
