import { createElement } from "react";
import type { ComponentProps } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import type { RadarEvent, RadarState } from "../lib/api";
import { FeedPanel } from "./FeedPanel";

const event: RadarEvent = {
  id: "event-one", first_seen_at: "2026-10-24T21:00:00Z",
  last_seen_at: "2026-10-24T21:00:00Z", resolved_at: null, status: "active",
  signal_type: "danger", threat_type: "uav", severity: 5, confidence: 0.5,
  source_count: 1, zone_id: "district", zone_path: ["district", "region"],
  place_name: "Тестовый район", zone_level: "district", lat: 50, lon: 40,
  accuracy_m: null, target_count: null,
};
const state: RadarState = {
  generated_at: "2026-10-24T21:01:00Z", last_message_at: "2026-10-24T21:00:00Z",
  last_event_at: "2026-10-24T21:00:00Z", data_age_sec: 60, pipeline_lag_sec: 0,
  stale: false, events: [event], zone_counts: {}, active_events: 1, active_zones: 1,
};

function render(overrides: Partial<ComponentProps<typeof FeedPanel>> = {}) {
  const props: ComponentProps<typeof FeedPanel> = {
    events: [event], state, apiOnline: true, selectedName: "Тестовый регион",
    selectedZoneId: "region", zoneEvents: [event], zoneEventsFromRegion: false,
    regionName: "Тестовый регион", bookmarks: [], onClearSelection: vi.fn(),
    onPickEvent: vi.fn(), onToggleBookmark: vi.fn(), historyLabel: null,
    referenceIso: null, collapsed: false, onCollapse: vi.fn(), onlyVisible: true,
    onToggleVisible: vi.fn(), levelFilter: [], onToggleLevel: vi.fn(),
    threatFilter: [], onToggleThreat: vi.fn(), totalEvents: 1, ...overrides,
  };
  return renderToStaticMarkup(createElement(FeedPanel, props));
}

describe("feed availability", () => {
  it("preserves selected region and events when an update fails", () => {
    const html = render({ apiOnline: false });
    expect(html).toContain("Тестовый регион");
    expect(html).toContain("Тестовый район");
    expect(html).toContain('class="event-feed"');
    expect(html).toContain("Обновление задерживается");
    expect(html).not.toContain("Обновите страницу");
  });

  it("does not replace the archive with a connection error", () => {
    const html = render({ apiOnline: false, historyLabel: "25 октября, 00:00" });
    expect(html).toContain("Показана обстановка на 25 октября, 00:00");
    expect(html).toContain("Тестовый район");
    expect(html).not.toContain("Обновление задерживается");
  });

  it("shows automatic reconnection before the first snapshot arrives", () => {
    const html = render({ apiOnline: false, state: null });
    expect(html).toContain("Повторяем подключение");
    expect(html).not.toContain("Обновите страницу");
  });

  it("returns to the normal feed after reconnection", () => {
    const html = render();
    expect(html).toContain("Тестовый район");
    expect(html).not.toContain("Обновление задерживается");
  });
});
