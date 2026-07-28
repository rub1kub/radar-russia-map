/**
 * Восстановление обстановки на произвольный момент.
 *
 * История считается на клиенте из выгрузки событий: у каждого есть начало,
 * конец и момент отбоя, поэтому срез на любое время получается фильтрацией.
 * Сервер отдаёт окно целиком один раз, дальше перемотка мгновенная.
 */

import type { RadarEvent, ZoneCount } from "./api";
import { freshness } from "./paint";

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
 * Счётчики зон на момент — та же форма и та же арифметика, что в
 * api/server.py build_state(), чтобы карта красилась одним кодом и в живом
 * режиме, и в истории. Расхождение здесь означало бы, что перемотка на
 * «сейчас» показывает не то же самое, что живая карта.
 */
export function zoneCountsAt(
  events: RadarEvent[],
  atIso: string,
  meta: Record<string, ZoneCount>
): Record<string, ZoneCount> {
  const counts: Record<string, ZoneCount> = {};
  const atMs = new Date(atIso).getTime();

  for (const event of eventsAt(events, atIso)) {
    for (const zoneId of event.zone_path) {
      // Скорость выцветания зависит от размера зоны и скорости цели:
      // район борт пересекает за минуты, регион — за часы.
      const fade = freshness(
        event.last_seen_at,
        atMs,
        meta[zoneId]?.level,
        event.threat_type
      );
      const weight = event.severity * fade;
      const bucket = counts[zoneId] ?? {
        active: 0,
        own: 0,
        max_severity: 0,
        severity: 0,
        own_severity: 0,
        fade: 1,
        own_fade: 1,
        last_active: event.last_seen_at,
        level: meta[zoneId]?.level,
        source_id: meta[zoneId]?.source_id,
        name: meta[zoneId]?.name
      };
      bucket.active += 1;

      // Цвет выбирает самое весомое сейчас событие: уровень на свежесть.
      if (weight > bucket.severity * bucket.fade) {
        bucket.severity = event.severity;
        bucket.fade = fade;
      }
      if (zoneId === event.zone_id) {
        bucket.own += 1;
        if (weight > bucket.own_severity * bucket.own_fade) {
          bucket.own_severity = event.severity;
          bucket.own_fade = fade;
        }
      }

      bucket.max_severity = Math.max(bucket.max_severity, event.severity);
      if (event.last_seen_at > bucket.last_active) bucket.last_active = event.last_seen_at;
      counts[zoneId] = bucket;
    }
  }

  return counts;
}
