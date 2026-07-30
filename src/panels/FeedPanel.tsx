import { useRef, useState } from "react";
import { Bell, BellRing, Building2, ChevronDown, ChevronRight, Crosshair } from "lucide-react";
import { api } from "../lib/api";
import type { EventSource, RadarEvent, RadarState } from "../lib/api";
import type { Bookmark } from "../lib/bookmarks";
import { isBookmarked } from "../lib/bookmarks";
import { quietVerdict } from "../lib/feed";
import {
  durationMinutes,
  formatAge,
  formatDayTime,
  formatDuration,
  formatMoment,
  formatSince,
  plural,
  severityColor,
  signalLabel,
  threatLabel
} from "../lib/format";

// Мост важен ровно тем, кто смотрит на Крым: в шапке для всей страны он
// был шумом (и оттуда его убрали), в карточке крымской зоны он — ответ.
const BRIDGE_REGIONS = new Set(["Республика Крым", "Севастополь"]);

type Props = {
  events: RadarEvent[];
  state: RadarState | null;
  apiOnline: boolean | null;
  selectedName: string | null;
  selectedZoneId: string | null;
  zoneEvents: RadarEvent[];
  /** В самом месте сообщений нет — показана обстановка по его области. */
  zoneEventsFromRegion: boolean;
  /** Как называется эта область. */
  regionName: string | null;
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

const THREATS = ["uav", "fpv", "rocket", "kab", "bek", "aviation"];

// Сколько карточек рисуем. Больше шестидесяти человек всё равно не читает,
// а список ниже становится дороже самой карты.
const FEED_LIMIT = 60;

export function FeedPanel({
  events: allEvents,
  state,
  apiOnline,
  selectedName,
  selectedZoneId,
  zoneEvents,
  zoneEventsFromRegion,
  regionName,
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
  const [expanded, setExpanded] = useState<string | null>(null);
  const [sources, setSources] = useState<Record<string, EventSource[]>>({});
  // Порядок карточек, замороженный на время чтения. Лента пересобирается
  // каждые несколько секунд, и список успевал переехать между взглядом и
  // нажатием: человек метил в одно событие, раскрывалось другое.
  const frozen = useRef<string[] | null>(null);

  const toggleSources = (id: string) => {
    setExpanded((current) => (current === id ? null : id));
    if (sources[id]) return;
    api
      .eventSources(id)
      .then((payload) => setSources((current) => ({ ...current, [id]: payload.sources })))
      .catch(() => setSources((current) => ({ ...current, [id]: [] })));
  };

  const live = apiOnline && state && !state.stale && !historyLabel;
  const listed = zoneEvents.length ? zoneEvents : allEvents;

  // Пока какая-то карточка раскрыта, порядок держится: читающему важнее,
  // чтобы список не уезжал под курсором, чем чтобы он был отсортирован
  // посекундно. Новые события при этом не теряются — они встают в конец.
  if (!expanded) frozen.current = null;
  else if (!frozen.current) frozen.current = listed.map((event) => event.id);

  const order = frozen.current;
  const events = order
    ? [...listed].sort((left, right) => {
        const a = order.indexOf(left.id);
        const b = order.indexOf(right.id);
        return (a === -1 ? order.length : a) - (b === -1 ? order.length : b);
      })
    : listed;
  // В режиме истории отсчёт идёт от просматриваемого момента, иначе лента
  // показывала бы будущее относительно выбранного среза.
  const reference = referenceIso ?? state?.generated_at ?? new Date().toISOString();

  // Ответ на главный вопрос под тревогой: «можно уже выходить?» Половина
  // лент отбоев не пишет; когда окно пролёта у всех незакрытых событий
  // места вышло, говорим это прямо. Только в живом эфире: в архиве вопрос
  // не стоит.
  const quiet = selectedName && zoneEvents.length && !historyLabel
    ? quietVerdict(zoneEvents, reference)
    : null;

  // Перекрытый мост — только при выбранном Крыме и только в эфире: в
  // архиве статус относится к «сейчас», а не к просматриваемому моменту.
  const bridge = state?.bridge;
  const showBridge = Boolean(
    !historyLabel &&
      bridge?.closed &&
      selectedName &&
      (BRIDGE_REGIONS.has(selectedName) || (regionName && BRIDGE_REGIONS.has(regionName)))
  );

  // Событие могло получить последнее сообщение уже после просматриваемого
  // момента. Тогда «сколько прошло» считать не от него, а от среза: иначе
  // архивная карточка писала «только что» и делалась неотличимой от эфира.
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
                <span>
                  {selectedName}
                  {/* Издалека районы безлики: «Чистопольский район» ничего
                      не говорит, пока рядом не написано «Татарстан». */}
                  {regionName && regionName !== selectedName ? (
                    <span className="zone-card-region">{regionName}</span>
                  ) : null}
                </span>
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
              {/* Район бывает закрашен не своей тревогой, а областной. Тогда
                  «сообщений нет» — правда, но она не объясняет цвет, и
                  человек видит вместо ответа общий поток по стране. */}
              {/* Район бывает закрашен не своей тревогой, а областной.
                  «Здесь тихо» рядом с действующей тревогой читалось как
                  отбой — то есть как разрешение выходить из дома. Пишем
                  факт: своих сообщений нет, показана обстановка по области. */}
              <p className={zoneEvents.length ? undefined : "zone-card-quiet"}>
                {!zoneEvents.length
                  ? "Сообщений нет"
                  : zoneEventsFromRegion
                    ? `Своих сообщений нет. Ниже — обстановка по региону${
                        regionName ? `: ${regionName}` : ""
                      }`
                    : `${zoneEvents.length} ${plural(
                        zoneEvents.length,
                        "событие здесь",
                        "события здесь",
                        "событий здесь"
                      )}`}
              </p>
              {showBridge && bridge ? (
                <p className="zone-card-bridge">
                  Крымский мост перекрыт · {formatMoment(bridge.at, reference)}
                </p>
              ) : null}
              {quiet !== null ? (
                <p className="zone-card-verdict">
                  {/* После часа счёт идёт часами: «эфир молчит 121 минуту»
                      читалось как сбой, а не как ответ. Про борт — только
                      если его действительно видели: «опасность» без единой
                      фиксации борта не утверждает, и говорить о нём как о
                      бывшем здесь карта не вправе. */}
                  Отбоя не было, но эфир молчит {formatAge(quiet.minutes * 60)} —{" "}
                  {quiet.sighted
                    ? "за это время борт успевает покинуть зону."
                    : "если борт и шёл сюда, зону он бы уже покинул."}
                </p>
              ) : null}
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

            {/* Один переключатель вместо трёх безымянных точек: точки без
                подписей никто не понимал, а нажатая случайно молча резала
                ленту. «Важное» — это красный уровень: борт уже видят. */}
            <button
              type="button"
              className={`filter-chip ${levelFilter.includes(8) ? "is-on" : ""}`}
              onClick={() => onToggleLevel(8)}
              data-tip="Только фиксации, взрывы и громкие звуки — без тревог и предупреждений"
            >
              <i
                className="chip-dot"
                style={{ background: severityColor(9, 0.95) }}
                aria-hidden="true"
              />
              <span>Важное</span>
            </button>

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

          {/* Список обрезан до шестидесяти, а число сверху говорило про все
              события: «283 сообщения», карточек 60, догрузки нет. Читатель
              вправе знать, что видит не всё. */}
          <p className="feed-summary">
            <strong>{events.length}</strong>{" "}
            {plural(events.length, "событие", "события", "событий")}
            {events.length !== totalEvents ? (
              <span className="feed-total"> из {totalEvents}</span>
            ) : null}
            {events.length > FEED_LIMIT ? (
              <span className="feed-total"> · показаны первые {FEED_LIMIT}</span>
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
              {events.slice(0, FEED_LIMIT).map((event) => (
                <li
                  key={event.id}
                  className={
                    // В истории status рассказывает про сейчас, а не про
                    // просматриваемый момент: события того часа все были
                    // открыты, иначе они бы сюда не попали. Без этой оговорки
                    // архив писал «Отбой» над каждой карточкой.
                    !historyLabel && event.status === "resolved"
                      ? "is-resolved"
                      : !historyLabel && event.status === "fading"
                        ? "is-fading"
                        : undefined
                  }
                >
                  {/* Вся карточка — одна кнопка: нажатие показывает место на
                      карте и раскрывает сообщения, из которых событие сложено.
                      Отдельная кнопка «N источников» рядом с карточкой ломала
                      раскладку и заставляла целиться в мелкую мишень. */}
                  <button
                    type="button"
                    className={`event-row ${expanded === event.id ? "is-open" : ""}`}
                    aria-expanded={expanded === event.id}
                    onClick={() => {
                      onPickEvent(event);
                      toggleSources(event.id);
                    }}
                  >
                    {/* Отбой человек должен увидеть: раньше событие просто
                        исчезало, тревога молча тускнела, и вопрос «можно уже
                        выходить?» оставался без ответа. */}
                    <span
                      className="event-dot"
                      style={{
                        background:
                          !historyLabel && event.status === "resolved"
                            ? "rgba(126, 190, 150, 0.95)"
                            : severityColor(event.severity, 0.95)
                      }}
                      aria-hidden="true"
                    />
                    <span className="event-body">
                      <span className="event-title">
                        {event.place_name}
                        {/* Станица без района читается как чужая: человек
                            выбрал Динской район, увидел «Пластуновскую» и
                            решил, что ему показали соседей. Место обязано
                            называть, где оно. */}
                        {event.parent_name ? (
                          <span className="event-where">{event.parent_name}</span>
                        ) : null}
                      </span>
                      <span className="event-meta">
                        {!historyLabel && event.status === "resolved"
                          ? `Отбой · ${signalLabel(event.signal_type).toLowerCase()}`
                          : signalLabel(event.signal_type)}
                        {event.threat_type !== "unknown"
                          ? ` · ${threatLabel(event.threat_type)}`
                          : ""}
                        {/* Число целей — только когда его назвал источник.
                            «Массированный налёт» раньше превращался в ровно
                            10 целей: цифру выдумывал разбор, а карточка
                            выдавала её за факт ленты. */}
                        {event.target_count
                          ? ` · ${event.target_count} ${plural(
                              event.target_count,
                              "цель",
                              "цели",
                              "целей"
                            )}`
                          : event.massive
                            ? " · групповой налёт"
                            : ""}
                      </span>
                      <span className="event-since">
                        {/* До последнего подтверждения, а не до «сейчас»:
                            событие с последним сообщением час назад не идёт
                            час, оно шло сколько шло и затихло.

                            Событие в одно сообщение длительности не имеет, и
                            «шло только что» было бессмыслицей: показываем
                            просто, когда это было. */}
                        {durationMinutes(event.first_seen_at, clamp(event.last_seen_at)) >= 1
                          ? `шло ${formatDuration(event.first_seen_at, clamp(event.last_seen_at))} · `
                          : ""}
                        {/* В архиве «12 минут назад» — это относительно
                            просматриваемого момента, а не сегодняшнего дня.
                            Без оговорки лента читается как прямой эфир. */}
                        {/* Время тоже ограничено срезом: показывать
                            «28.07 06:42» на моменте 04:15 значит выдавать
                            то, чего в тот час ещё не знали. */}
                        {historyLabel
                          ? formatDayTime(clamp(event.last_seen_at))
                          : formatSince(clamp(event.last_seen_at), reference)}
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
                    <span className="event-aside">
                      <span className="event-time">
                        {formatMoment(clamp(event.last_seen_at), reference)}
                      </span>
                      <ChevronDown className="event-caret" size={13} aria-hidden="true" />
                    </span>
                  </button>

                  {expanded === event.id ? (
                    <ul className="event-sources">
                      {/* Сообщений почти всегда больше, чем засчитанных
                          источников: дословный перепост и повтор того же
                          канала не добавляют свидетельства. Без этой строки
                          число под заголовком выглядело бы взятым с потолка. */}
                      {sources[event.id]?.length > event.source_count ? (
                        <li className="source-note">
                          {sources[event.id].length}{" "}
                          {plural(
                            sources[event.id].length,
                            "сообщение",
                            "сообщения",
                            "сообщений"
                          )}
                          {", засчитано "}
                          {sources[event.id].filter((item) => item.counted).length}
                          {". Не в счёт: "}
                          {sources[event.id].filter((item) => item.repost).length} перепост,{" "}
                          {sources[event.id].filter((item) => item.clone).length} из той же сети
                        </li>
                      ) : null}
                      {(sources[event.id] ?? []).map((item, index) => (
                        <li
                          key={`${item.source_key}-${index}`}
                          className={item.counted ? undefined : "is-repost"}
                        >
                          <span className="source-head">
                            {/* Ник без ссылки — число «19 источников»,
                                которое остаётся принимать на веру. */}
                            {item.link ? (
                              <a
                                className="source-name"
                                href={item.link}
                                target="_blank"
                                rel="noreferrer noopener"
                                onClick={(click) => click.stopPropagation()}
                              >
                                {item.source_key}
                              </a>
                            ) : (
                              <span className="source-name">{item.source_key}</span>
                            )}
                            {item.repost ? <span className="source-tag">перепост</span> : null}
                            {item.clone && !item.repost ? (
                              <span className="source-tag">та же сеть</span>
                            ) : null}
                            <span className="source-time">
                              {formatMoment(item.at, reference)}
                            </span>
                          </span>
                          <span className="source-text">{item.text}</span>
                        </li>
                      ))}
                      {sources[event.id] && !sources[event.id].length ? (
                        <li className="source-empty">Источники недоступны</li>
                      ) : null}
                      {!sources[event.id] ? <li className="source-empty">Загрузка…</li> : null}
                    </ul>
                  ) : null}
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
