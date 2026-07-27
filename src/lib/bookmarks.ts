/**
 * Отслеживаемые места.
 *
 * Главная причина, по которой на такие карты возвращаются: человека волнует
 * свой город, а не общая картина. Хранится только в браузере — никаких
 * учётных записей и никакой отправки на сервер.
 */

import type { RadarEvent } from "./api";

const STORAGE_KEY = "radar.bookmarks.v1";
const SEEN_KEY = "radar.seen-events.v1";

export type Bookmark = {
  zone_id: string;
  name: string;
  level: "region" | "district" | "place";
  context: string | null;
  lat: number | null;
  lon: number | null;
  source_id: string | null;
};

function readJson<T>(key: string, fallback: T): T {
  try {
    const raw = window.localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    // Приватный режим или испорченное значение — работаем без сохранения.
    return fallback;
  }
}

function writeJson(key: string, value: unknown): void {
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // Переполнение или запрет записи не должны ломать карту.
  }
}

export function loadBookmarks(): Bookmark[] {
  return readJson<Bookmark[]>(STORAGE_KEY, []);
}

export function saveBookmarks(items: Bookmark[]): void {
  writeJson(STORAGE_KEY, items);
}

export function toggleBookmark(items: Bookmark[], candidate: Bookmark): Bookmark[] {
  const exists = items.some((item) => item.zone_id === candidate.zone_id);
  const next = exists
    ? items.filter((item) => item.zone_id !== candidate.zone_id)
    : [...items, candidate];
  saveBookmarks(next);
  return next;
}

export function isBookmarked(items: Bookmark[], zoneId: string): boolean {
  return items.some((item) => item.zone_id === zoneId);
}

/**
 * События, затрагивающие отслеживаемые места.
 *
 * Совпадение по всей цепочке зон: тревога по области поднимает уведомление
 * и для отслеживаемого посёлка внутри неё.
 */
export function matchBookmarks(events: RadarEvent[], items: Bookmark[]): RadarEvent[] {
  if (!items.length) return [];
  const watched = new Set(items.map((item) => item.zone_id));
  return events.filter(
    (event) => watched.has(event.zone_id) || event.zone_path.some((zone) => watched.has(zone))
  );
}

export function loadSeen(): string[] {
  return readJson<string[]>(SEEN_KEY, []);
}

/** Отметить события показанными, чтобы уведомление не всплывало повторно. */
export function markSeen(ids: string[]): void {
  const merged = Array.from(new Set([...loadSeen(), ...ids]));
  // Список не должен расти бесконечно.
  writeJson(SEEN_KEY, merged.slice(-500));
}
