import { describe, expect, it } from "vitest";
import { quietVerdict, zoneFeed } from "./feed";
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

describe("quietVerdict", () => {
  // Тревога по району, БПЛА: окно пролёта 35 минут.
  const alarm = event("azov", ["azov", "rostov"]);

  it("окно ещё не вышло — вердикта нет, тревога в силе", () => {
    // 20 минут тишины при окне в 35: борт ещё может быть в зоне.
    expect(quietVerdict([alarm], "2026-07-28T21:30:00Z")).toBeNull();
  });

  it("окно вышло — возвращает минуты тишины", () => {
    // 50 минут после последнего сообщения: район борт пересекает за 35.
    expect(quietVerdict([alarm], "2026-07-28T22:00:00Z")?.minutes).toBe(50);
  });

  it("предупреждение без фиксации борта не утверждает", () => {
    // «Опасность» и «тревога» — гипотеза; говорить «борт покинул зону»
    // карта вправе, только если борт действительно видели.
    expect(quietVerdict([alarm], "2026-07-28T22:00:00Z")?.sighted).toBe(false);
    const seen = { ...event("bataysk", ["bataysk", "rostov"]), signal_type: "detection" };
    expect(quietVerdict([alarm, seen], "2026-07-28T22:00:00Z")?.sighted).toBe(true);
  });

  it("считает по самому живучему событию", () => {
    // Первое молчит давно, второе свежее: пока у него окно не вышло,
    // вердикта нет — иначе старая карточка «разрешала выходить» при
    // действующей тревоге по соседству.
    const fresh = { ...event("bataysk", ["bataysk", "rostov"]), last_seen_at: "2026-07-28T21:50:00Z" };
    expect(quietVerdict([alarm, fresh], "2026-07-28T22:00:00Z")).toBeNull();
  });

  it("закрытые события вердикта не требуют: отбой уже показан", () => {
    const resolved = { ...alarm, status: "resolved" };
    expect(quietVerdict([resolved], "2026-07-28T23:00:00Z")).toBeNull();
  });

  it("ракета покидает зону быстрее дрона", () => {
    // Для ракеты окно района 35 * 0.2 = 7, но не меньше пола в 8 минут.
    const rocket = { ...alarm, threat_type: "rocket" };
    expect(quietVerdict([rocket], "2026-07-28T21:20:00Z")?.minutes).toBe(10);
  });
});
