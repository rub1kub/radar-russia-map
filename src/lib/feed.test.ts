import { describe, expect, it } from "vitest";
import { zoneFeed } from "./feed";
import type { RadarEvent } from "./api";

const event = (id: string, path: string[]): RadarEvent =>
  ({
    id,
    zone_id: path[0],
    zone_path: path,
    severity: 7,
    signal_type: "alarm",
    threat_type: "uav",
    first_seen_at: "2026-07-28T21:00:00Z",
    last_seen_at: "2026-07-28T21:10:00Z",
    resolved_at: null,
    status: "active",
    source_count: 2,
    confidence: 0.8,
    lat: 47,
    lon: 39,
    accuracy_m: 12000,
    direction_deg: null,
    target_count: null,
    place_name: id,
    zone_level: "district"
  }) as RadarEvent;

const ROSTOV = [
  event("azov", ["azov", "rostov"]),
  event("bataysk", ["bataysk", "rostov"]),
  event("rostov-region", ["rostov"])
];
const KRASNODAR = [event("yeysk", ["yeysk", "krasnodar"])];
const ALL = [...ROSTOV, ...KRASNODAR];

describe("zoneFeed", () => {
  it("у места со своими событиями показываются они", () => {
    const feed = zoneFeed(ALL, "azov", "rostov");
    expect(feed.events.map((e) => e.id)).toEqual(["azov"]);
    expect(feed.fromRegion).toBe(false);
  });

  it("в тихом районе показывается обстановка его области", () => {
    // Ради этого правило и появилось: раньше здесь была лента всей страны,
    // и под тихим районом Ростовской области висели тревоги Краснодарского.
    const feed = zoneFeed(ALL, null, "rostov");
    expect(feed.events.map((e) => e.id)).toEqual(["azov", "bataysk", "rostov-region"]);
    expect(feed.fromRegion).toBe(true);
  });

  it("зона района известна, но событий в ней нет — тот же ответ", () => {
    const feed = zoneFeed(ALL, "kagalnitskiy", "rostov");
    expect(feed.fromRegion).toBe(true);
    expect(feed.events.every((e) => e.zone_path.includes("rostov"))).toBe(true);
  });

  it("тихая область не подставляет саму себя", () => {
    const feed = zoneFeed(KRASNODAR, "kalmykia", "kalmykia");
    expect(feed.events).toEqual([]);
    expect(feed.fromRegion).toBe(false);
  });

  it("без известной области ответа нет, но и чужого не показываем", () => {
    expect(zoneFeed(ALL, null, null).events).toEqual([]);
  });
});
