/**
 * Восстановление обстановки на произвольный момент.
 *
 * История считается на клиенте из выгрузки событий: у каждого есть начало,
 * конец и момент отбоя, поэтому срез на любое время получается фильтрацией.
 * Сервер отдаёт окно целиком один раз, дальше перемотка мгновенная.
 */

import type { RadarEvent, ZoneCount } from "./api";

export const SLOT_MS = 15 * 60 * 1000;

export type Slot = {
  at: string;
  label: string;
};

/** Разбить окно на срезы с шагом 15 минут, как это делает RadarMap. */
export function buildSlots(fromIso: string, toIso: string): Slot[] {
  const from = new Date(fromIso).getTime();
  const to = new Date(toIso).getTime();
  const slots: Slot[] = [];
  for (let at = Math.ceil(from / SLOT_MS) * SLOT_MS; at <= to; at += SLOT_MS) {
    const moment = new Date(at);
    slots.push({ at: moment.toISOString(), label: moment.toISOString() });
  }
  return slots;
}

/** Было ли событие активно в указанный момент. */
export function activeAt(event: RadarEvent, atMs: number): boolean {
  const start = new Date(event.first_seen_at).getTime();
  if (start > atMs) return false;

  const closed = event.resolved_at ? new Date(event.resolved_at).getTime() : null;
  if (closed !== null && closed <= atMs) return false;

  // После последнего сообщения событие ещё три часа считается затухающим —
  // тот же порог, что и в pipeline/fuse.py.
  const last = new Date(event.last_seen_at).getTime();
  return atMs - last <= 3 * 60 * 60 * 1000;
}

export function eventsAt(events: RadarEvent[], atIso: string): RadarEvent[] {
  const atMs = new Date(atIso).getTime();
  return events.filter((event) => activeAt(event, atMs));
}

/**
 * Счётчики зон на момент — та же форма, что отдаёт /api/v1/state,
 * чтобы карта красилась одним и тем же кодом и в живом режиме, и в истории.
 */
export function zoneCountsAt(
  events: RadarEvent[],
  atIso: string,
  meta: Record<string, ZoneCount>
): Record<string, ZoneCount> {
  const counts: Record<string, ZoneCount> = {};

  for (const event of eventsAt(events, atIso)) {
    for (const zoneId of event.zone_path) {
      const bucket = counts[zoneId] ?? {
        active: 0,
        max_severity: 0,
        last_active: event.last_seen_at,
        level: meta[zoneId]?.level,
        source_id: meta[zoneId]?.source_id,
        name: meta[zoneId]?.name
      };
      bucket.active += 1;
      bucket.max_severity = Math.max(bucket.max_severity, event.severity);
      if (event.last_seen_at > bucket.last_active) bucket.last_active = event.last_seen_at;
      counts[zoneId] = bucket;
    }
  }

  return counts;
}
