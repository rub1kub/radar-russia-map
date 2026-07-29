/** Типы и обращения к API конвейера. */

export const API_BASE = import.meta.env.VITE_API_BASE || "http://127.0.0.1:8000";

export type RadarEvent = {
  id: string;
  first_seen_at: string;
  last_seen_at: string;
  resolved_at: string | null;
  status: string;
  signal_type: string;
  threat_type: string;
  severity: number;
  confidence: number;
  source_count: number;
  zone_id: string;
  zone_path: string[];
  place_name: string;
  /** Где это место: район для НП, область для района. */
  parent_name?: string | null;
  zone_level: string;
  lat: number | null;
  lon: number | null;
  accuracy_m: number | null;
  /** Откуда пришла цель, азимут в градусах: 0 — с севера. */
  direction_deg?: number | null;
  target_count: number | null;
};

export type ZoneCount = {
  active: number;
  /** Сколько событий названо самой этой зоной, а не унаследовано от вложенных. */
  own: number;
  max_severity: number;
  /** Уровень события, самого весомого сейчас: уровень, взвешенный свежестью. */
  severity: number;
  /** Свежесть этого события: 1.0 — только что, 0.25 — три часа и старше. */
  fade: number;
  /** То же, но только по собственным событиям зоны. */
  own_severity: number;
  own_fade: number;
  last_active: string;
  level?: "region" | "district" | "place";
  source_id?: string;
  name?: string;
};

export type RadarState = {
  generated_at: string;
  /** Крымский мост: показывается только перекрытие. null — данных нет. */
  bridge?: { closed: boolean; at: string } | null;
  last_message_at: string;
  data_age_sec: number;
  last_event_at: string | null;
  /** Отставание разбора от сбора. Растёт, если конвейер встал. */
  pipeline_lag_sec: number | null;
  stale: boolean;
  events: RadarEvent[];
  zone_counts: Record<string, ZoneCount>;
  active_events: number;
  active_zones: number;
};

export type SearchItem = {
  zone_id: string;
  name: string;
  level: "region" | "district" | "place";
  context: string | null;
  lat: number | null;
  lon: number | null;
  source_id: string | null;
};

export type SourceStat = {
  /** official — МЧС, оперштабы, губернаторы; остальные ленты неофициальны. */
  tier?: string;
  source_key: string;
  messages: number;
  contributions: number;
  first_reports: number;
  confirmations: number;
  confirmed_share: number;
  unconfirmed_share: number;
  median_lag_sec: number | null;
};

export type ZoneStat = {
  name_ru: string;
  level: string;
  zone_id: string;
  events: number;
  max_severity: number;
  avg_confidence: number;
  avg_duration_sec: number;
};

export type EventSource = {
  source_key: string;
  role: string;
  at: string;
  first_from_source: boolean;
  /** Дословный перепост уже принесённого текста: в подтверждение не идёт. */
  repost: boolean;
  /** Канал той же сети, что и уже засчитанный: у сети один голос. */
  clone: boolean;
  /** Пошло ли сообщение в счёт независимых источников. */
  counted: boolean;
  /** Постоянная ссылка на сообщение в Telegram. */
  link: string | null;
  text: string;
};

export type Analytics = {
  top_zones: ZoneStat[];
  by_hour: Array<{ hour: string; n: number }>;
  by_threat: Array<{ threat_type: string; n: number }>;
};

export type History = {
  from: string;
  to: string;
  day?: string;
  events: RadarEvent[];
};

export type HistoryDay = {
  /** Сколько каналов отчитывалось в этот день: по нему видно, шёл ли сбор. */
  sources?: number;
  day: string;
  events: number;
  confirmed: number;
  max_severity: number;
  /** Доля от самого насыщенного дня: из неё рисуется полоска. */
  density: number;
};

async function get<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, { signal });
  if (!response.ok) throw new Error(`${path}: ${response.status}`);
  return (await response.json()) as T;
}

export const api = {
  state: (signal?: AbortSignal) => get<RadarState>("/api/v1/state", signal),
  history: (hours: number, signal?: AbortSignal) =>
    get<History>(`/api/v1/history?hours=${hours}`, signal),
  historyDay: (day: string, signal?: AbortSignal) =>
    get<History>(`/api/v1/history?day=${encodeURIComponent(day)}`, signal),
  eventSources: (id: string, signal?: AbortSignal) =>
    get<{ sources: EventSource[]; counted: number }>(
      `/api/v1/events/${encodeURIComponent(id)}/sources`,
      signal
    ),
  fires: (signal?: AbortSignal) =>
    get<{ points: Array<[number, number, number]>; updated: string | null }>(
      "/api/v1/fires",
      signal
    ),
  historyDays: (limit: number, signal?: AbortSignal) =>
    get<{ days: HistoryDay[]; peak: number }>(`/api/v1/history/days?limit=${limit}`, signal),
  analyticsSources: (signal?: AbortSignal) =>
    get<{ sources: SourceStat[]; since: string | null; until: string | null }>(
      "/api/v1/analytics/sources",
      signal
    ),
  analyticsZones: (hours: number, signal?: AbortSignal) =>
    get<Analytics>(`/api/v1/analytics/zones?hours=${hours}`, signal),
  search: (query: string, limit: number, signal?: AbortSignal) =>
    get<{ items: SearchItem[] }>(
      `/api/v1/search?q=${encodeURIComponent(query)}&limit=${limit}`,
      signal
    )
};
