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
  zone_level: string;
  lat: number | null;
  lon: number | null;
  accuracy_m: number | null;
  target_count: number | null;
};

export type ZoneCount = {
  active: number;
  max_severity: number;
  last_active: string;
  level?: "region" | "district" | "place";
  source_id?: string;
  name?: string;
};

export type RadarState = {
  generated_at: string;
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

export type Analytics = {
  top_zones: ZoneStat[];
  by_hour: Array<{ hour: string; n: number }>;
  by_threat: Array<{ threat_type: string; n: number }>;
};

export type History = {
  from: string;
  to: string;
  events: RadarEvent[];
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
  analyticsSources: (signal?: AbortSignal) =>
    get<{ sources: SourceStat[] }>("/api/v1/analytics/sources", signal),
  analyticsZones: (hours: number, signal?: AbortSignal) =>
    get<Analytics>(`/api/v1/analytics/zones?hours=${hours}`, signal),
  search: (query: string, limit: number, signal?: AbortSignal) =>
    get<{ items: SearchItem[] }>(
      `/api/v1/search?q=${encodeURIComponent(query)}&limit=${limit}`,
      signal
    )
};
