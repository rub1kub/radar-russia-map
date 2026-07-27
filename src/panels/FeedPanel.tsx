import { Bell, BellRing, Building2, ChevronRight, Crosshair } from "lucide-react";
import type { RadarEvent, RadarState } from "../lib/api";
import type { Bookmark } from "../lib/bookmarks";
import { isBookmarked } from "../lib/bookmarks";
import {
  formatAge,
  formatDuration,
  formatMoment,
  plural,
  severityColor,
  signalLabel,
  threatLabel
} from "../lib/format";

type Props = {
  events: RadarEvent[];
  state: RadarState | null;
  apiOnline: boolean | null;
  selectedName: string | null;
  selectedZoneId: string | null;
  zoneEvents: RadarEvent[];
  bookmarks: Bookmark[];
  onClearSelection: () => void;
  onPickEvent: (event: RadarEvent) => void;
  onToggleBookmark: () => void;
  historyLabel: string | null;
  /** Момент, относительно которого считается время: сейчас или срез истории. */
  referenceIso: string | null;
  collapsed: boolean;
  onCollapse: () => void;
  onlyVisible: boolean;
  onToggleVisible: () => void;
  levelFilter: number[];
  onToggleLevel: (level: number) => void;
  threatFilter: string[];
  onToggleThreat: (threat: string) => void;
  /** Сколько событий всего, до применения фильтров. */
  totalEvents: number;
};

const LEVELS = [
  { value: 8, tip: "Ракета, взрыв, работа ПВО" },
  { value: 6, tip: "Тревога" },
  { value: 4, tip: "Опасность и фиксации" }
];

const THREATS = ["uav", "fpv", "rocket", "kab", "bek", "aviation"];

export function FeedPanel({
  events: allEvents,
  state,
  apiOnline,
  selectedName,
  selectedZoneId,
  zoneEvents,
  bookmarks,
  onClearSelection,
  onPickEvent,
  onToggleBookmark,
  historyLabel,
  referenceIso,
  collapsed,
  onCollapse,
  onlyVisible,
  onToggleVisible,
  levelFilter,
  onToggleLevel,
  threatFilter,
  onToggleThreat,
  totalEvents
}: Props) {
  const live = apiOnline && state && !state.stale && !historyLabel;
  const events = zoneEvents.length ? zoneEvents : allEvents;
  // В режиме истории отсчёт идёт от просматриваемого момента, иначе лента
  // показывала бы будущее относительно выбранного среза.
  const reference = referenceIso ?? state?.generated_at ?? new Date().toISOString();

  const clamp = (iso: string) => (new Date(iso) > new Date(reference) ? reference : iso);

  return (
    <aside
      className={`details-panel ${collapsed ? "is-collapsed" : ""}`}
      aria-label="Обстановка"
      aria-hidden={collapsed}
    >
      <div className="feed-top">
        <button
          className="panel-collapse"
          type="button"
          onClick={onCollapse}
          aria-label="Свернуть ленту"
        >
          <ChevronRight size={16} aria-hidden="true" />
        </button>
        <h2>{historyLabel ? "Было в эфире" : "Что происходит"}</h2>
        <span
          className={`live-dot ${live ? "is-live" : "is-off"}`}
          title={live ? "Данные обновляются" : historyLabel ? "Просмотр истории" : "Нет свежих данных"}
          aria-hidden="true"
        />
      </div>

      {apiOnline === false ? (
        <p className="feed-empty">Нет связи с сервером. Обновите страницу.</p>
      ) : !state ? (
        <p className="feed-empty">Загрузка…</p>
      ) : (
        <>
          {historyLabel ? (
            <p className="history-banner">Показана обстановка на {historyLabel}</p>
          ) : state.stale ? (
            <p className="stale-banner">
              Сбор сообщений остановлен {formatAge(state.data_age_sec)} назад. Показанное не
              отражает текущую обстановку.
            </p>
          ) : null}

          {selectedName ? (
            <div className="zone-card">
              <div className="zone-card-head">
                <Building2 size={15} aria-hidden="true" />
                <span>{selectedName}</span>
                {selectedZoneId ? (
                  <button
                    type="button"
                    className={`bookmark-toggle ${
                      isBookmarked(bookmarks, selectedZoneId) ? "is-on" : ""
                    }`}
                    onClick={onToggleBookmark}
                    title={
                      isBookmarked(bookmarks, selectedZoneId)
                        ? "Не отслеживать"
                        : "Отслеживать это место"
                    }
                  >
                    {isBookmarked(bookmarks, selectedZoneId) ? (
                      <BellRing size={15} aria-hidden="true" />
                    ) : (
                      <Bell size={15} aria-hidden="true" />
                    )}
                  </button>
                ) : null}
                <button type="button" onClick={onClearSelection} aria-label="Снять выбор">
                  ×
                </button>
              </div>
              <p className={zoneEvents.length ? undefined : "zone-card-quiet"}>
                {zoneEvents.length
                  ? `${zoneEvents.length} ${plural(
                      zoneEvents.length,
                      "активное сообщение",
                      "активных сообщения",
                      "активных сообщений"
                    )}`
                  : "Сообщений нет"}
              </p>
            </div>
          ) : null}

          <div className="feed-filters">
            <button
              type="button"
              className={`filter-chip ${onlyVisible ? "is-on" : ""}`}
              onClick={onToggleVisible}
              data-tip="Показывать только то, что попадает в видимую часть карты"
            >
              <Crosshair size={13} aria-hidden="true" />
              <span>в кадре</span>
            </button>

            {LEVELS.map((level) => (
              <button
                key={level.value}
                type="button"
                className={`filter-dot ${levelFilter.includes(level.value) ? "is-on" : ""}`}
                onClick={() => onToggleLevel(level.value)}
                data-tip={level.tip}
                aria-label={level.tip}
              >
                <i style={{ background: severityColor(level.value, 0.95) }} aria-hidden="true" />
              </button>
            ))}

            {THREATS.map((threat) => (
              <button
                key={threat}
                type="button"
                className={`filter-chip ${threatFilter.includes(threat) ? "is-on" : ""}`}
                onClick={() => onToggleThreat(threat)}
              >
                {threatLabel(threat)}
              </button>
            ))}
          </div>

          <p className="feed-summary">
            <strong>{events.length}</strong>{" "}
            {plural(events.length, "сообщение", "сообщения", "сообщений")}
            {events.length !== totalEvents ? (
              <span className="feed-total"> из {totalEvents}</span>
            ) : null}
          </p>

          {events.length === 0 ? (
            <p className="feed-empty">
              {historyLabel
                ? "На этот момент сообщений не было."
                : totalEvents
                  ? "В этой части карты сообщений нет. Отдалите карту или снимите фильтры."
                  : "Сейчас активных сообщений нет."}
            </p>
          ) : (
            <ul className="event-feed">
              {events.slice(0, 60).map((event) => (
                <li
                  key={event.id}
                  className={event.status === "fading" ? "is-fading" : undefined}
                >
                  <button type="button" onClick={() => onPickEvent(event)}>
                    <span
                      className="event-dot"
                      style={{ background: severityColor(event.severity, 0.95) }}
                      aria-hidden="true"
                    />
                    <span className="event-body">
                      <span className="event-title">{event.place_name}</span>
                      <span className="event-meta">
                        {signalLabel(event.signal_type)}
                        {event.threat_type !== "unknown"
                          ? ` · ${threatLabel(event.threat_type)}`
                          : ""}
                        {event.target_count ? ` · ${event.target_count} целей` : ""}
                      </span>
                      <span className="event-since">
                        идёт {formatDuration(event.first_seen_at, reference)}
                        {event.source_count > 1
                          ? ` · ${event.source_count} ${plural(
                              event.source_count,
                              "источник",
                              "источника",
                              "источников"
                            )}`
                          : ""}
                      </span>
                    </span>
                    <span className="event-time">
                      {formatMoment(clamp(event.last_seen_at), reference)}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}

          <p className="feed-foot">Время московское. Данные из открытых Telegram-каналов.</p>
        </>
      )}
    </aside>
  );
}
