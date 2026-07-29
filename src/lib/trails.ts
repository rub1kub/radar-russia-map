/**
 * Следы налёта в архиве: однозначные звенья между точечными событиями.
 *
 * Это ДОГАДКА, и потому правила жёсткие. Звено рисуется, только когда у
 * события ровно одно правдоподобное продолжение: соседняя зона, та же
 * угроза, время сходится со скоростью борта. На живой неделе таких — 183
 * из двух тысяч точечных событий; у тысячи нашлось несколько кандидатов
 * сразу (массовый налёт), и им не рисуется ничего — склеивать «кто куда
 * полетел» в налёт значит врать. Поэтому следы живут только в плеере
 * истории, пунктиром: в прямом эфире карта утверждает лишь то, что
 * сказали источники.
 */

import type { RadarEvent } from "./api";

export type Trail = {
  from: [number, number];
  to: [number, number];
  /** Момент позднего конца звена — от него след стареет. */
  at: string;
  severity: number;
};

const SIGHTING_SIGNALS = new Set(["detection", "intercept", "impact"]);
// Раньше двух минут борт не долетает до соседней зоны; позже пятидесяти —
// звено уже неотличимо от следующей волны.
const MIN_GAP_MIN = 2;
const MAX_GAP_MIN = 50;
/** Сколько след остаётся на карте после позднего конца звена. */
export const TRAIL_TTL_MS = 60 * 60 * 1000;

function distKm(a: RadarEvent, b: RadarEvent): number {
  const dx =
    ((b.lon as number) - (a.lon as number)) *
    111 *
    Math.cos((((a.lat as number) + (b.lat as number)) / 2 / 180) * Math.PI);
  const dy = ((b.lat as number) - (a.lat as number)) * 111;
  return Math.hypot(dx, dy);
}

/**
 * Все однозначные звенья выборки. Порог расстояния растёт со временем
 * зазора — как далеко успевает уйти борт на ~150 км/ч плюс размер зоны.
 * Только БПЛА: ракета пролетает зону между двумя сообщениями целиком,
 * и время публикаций о ней ничего не говорит о пути.
 */
export function inferTrails(events: RadarEvent[]): Trail[] {
  const points = events
    .filter(
      (event) =>
        SIGHTING_SIGNALS.has(event.signal_type) &&
        event.threat_type === "uav" &&
        event.zone_level !== "region" &&
        typeof event.lat === "number" &&
        typeof event.lon === "number"
    )
    .sort((left, right) => left.first_seen_at.localeCompare(right.first_seen_at));

  const trails: Trail[] = [];
  for (let i = 0; i < points.length; i += 1) {
    const a = points[i];
    const started = new Date(a.first_seen_at).getTime();
    let candidate: RadarEvent | null = null;
    let count = 0;

    for (let j = i + 1; j < points.length; j += 1) {
      const b = points[j];
      const gapMin = (new Date(b.first_seen_at).getTime() - started) / 60_000;
      if (gapMin > MAX_GAP_MIN) break;
      if (gapMin < MIN_GAP_MIN || b.zone_id === a.zone_id) continue;
      const km = distKm(a, b);
      if (km > 5 && km < Math.min(160, 10 + gapMin * 3.5)) {
        candidate = b;
        count += 1;
        if (count > 1) break;
      }
    }

    if (count === 1 && candidate) {
      trails.push({
        from: [a.lat as number, a.lon as number],
        to: [candidate.lat as number, candidate.lon as number],
        at: candidate.first_seen_at,
        severity: candidate.severity
      });
    }
  }
  return trails;
}

/** Виден ли след в просматриваемый момент архива. */
export function trailVisibleAt(trail: Trail, atMs: number): boolean {
  const ended = new Date(trail.at).getTime();
  return ended <= atMs && atMs - ended < TRAIL_TTL_MS;
}
