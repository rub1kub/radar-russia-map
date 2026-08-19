import { BellRing, Send, Volume2, VolumeX, X } from "lucide-react";
import type { RadarState } from "../lib/api";
import type { Bookmark } from "../lib/bookmarks";
import { plural, severityColor } from "../lib/format";
import { insideTelegram } from "../lib/telegram";

/**
 * Диплинк бота: первое из мест человека уходит в start-payload, и бот
 * подписывает на него одним нажатием. Payload в Telegram ограничен
 * 64 знаками; редкий длинный id просто открывает бота без подписки.
 */
function botLink(bookmarks: Bookmark[]): string {
  const base = "https://t.me/Tihoeneborobot";
  const zoneId = bookmarks[0]?.zone_id;
  if (zoneId && zoneId.length <= 62) return `${base}?start=w_${zoneId}`;
  return base;
}

type Props = {
  bookmarks: Bookmark[];
  state: RadarState | null;
  onPick: (bookmark: Bookmark) => void;
  onRemove: (zoneId: string) => void;
  soundOn: boolean;
  onToggleSound: () => void;
  /** null — браузер пушей не умеет, кнопка не рисуется. */
  pushOn: boolean | null;
  onTogglePush: () => void;
};

/**
 * Список отслеживаемых мест.
 *
 * Закладки существовали только как колокольчик на выбранной зоне — увидеть
 * все свои места разом было негде, а именно ради них человек и возвращается.
 */
export function BookmarksSection({
  bookmarks,
  state,
  onPick,
  onRemove,
  soundOn,
  onToggleSound,
  pushOn,
  onTogglePush
}: Props) {
  return (
    <section className="tool-section bookmarks-section">
      <div className="section-heading">
        <BellRing size={16} aria-hidden="true" />
        <h2>Мои места</h2>
        {/* Обе кнопки — одной группой у правого края, иначе они
            расползаются по строке заголовка. */}
        <span className="section-actions">
          {/* Пуш догоняет закрытую вкладку: тревога и отбой приходят
              системным уведомлением. Только по явному выбору. */}
          {pushOn !== null ? (
            <button
              type="button"
              className={`sound-toggle ${pushOn ? "is-on" : ""}`}
              onClick={onTogglePush}
              title={
                pushOn
                  ? "Выключить уведомления при закрытой вкладке"
                  : "Уведомлять даже при закрытой вкладке"
              }
              aria-label={
                pushOn
                  ? "Выключить уведомления при закрытой вкладке"
                  : "Уведомлять даже при закрытой вкладке"
              }
              aria-pressed={pushOn}
            >
              <Send size={15} aria-hidden="true" />
            </button>
          ) : null}
          {/* Звук выключен по умолчанию: в этой теме он должен быть осознанным
              выбором. Кнопка и есть жест, которым браузер разрешает звук. */}
          <button
            type="button"
            className={`sound-toggle ${soundOn ? "is-on" : ""}`}
            onClick={onToggleSound}
            title={soundOn ? "Выключить звук тревоги" : "Включить звук тревоги"}
            aria-label={soundOn ? "Выключить звук тревоги" : "Включить звук тревоги"}
            aria-pressed={soundOn}
          >
            {soundOn ? <Volume2 size={15} aria-hidden="true" /> : <VolumeX size={15} aria-hidden="true" />}
          </button>
        </span>
      </div>

      {bookmarks.length === 0 ? (
        <p className="bookmarks-hint">
          Выберите район на карте и нажмите колокольчик — он появится здесь,
          а при тревоге всплывёт предупреждение.
        </p>
      ) : (
        <ul className="bookmarks-list">
          {bookmarks.map((bookmark) => {
            const zone = state?.zone_counts?.[bookmark.zone_id];
            const active = zone?.active ?? 0;

            return (
              <li key={bookmark.zone_id}>
                <button type="button" onClick={() => onPick(bookmark)}>
                  <span
                    className="bookmark-dot"
                    style={{
                      background: active
                        ? severityColor(zone?.max_severity ?? 5, 0.95)
                        : "rgba(120, 132, 125, 0.5)"
                    }}
                    aria-hidden="true"
                  />
                  <span className="bookmark-text">
                    <span className="bookmark-name">{bookmark.name}</span>
                    <span className="bookmark-status">
                      {active
                        ? `${active} ${plural(active, "сообщение", "сообщения", "сообщений")}`
                        : "спокойно"}
                    </span>
                  </span>
                </button>
                <button
                  type="button"
                  className="bookmark-remove"
                  onClick={() => onRemove(bookmark.zone_id)}
                  aria-label={`Убрать ${bookmark.name}`}
                >
                  <X size={13} aria-hidden="true" />
                </button>
              </li>
            );
          })}
        </ul>
      )}

      {/* Воронка бота. В мини-аппе не показывается: там колокольчик и так
          подписывает на сообщения в чат. */}
      {!insideTelegram() && (
        <a
          className="tg-funnel"
          href={botLink(bookmarks)}
          target="_blank"
          rel="noreferrer"
          title={
            bookmarks.length
              ? `Бот сразу подпишет на «${bookmarks[0].name}»`
              : "Бот пришлёт тревогу и отбой сообщением"
          }
        >
          <Send size={13} aria-hidden="true" />
          Эти уведомления есть и в Telegram — подключить бота
        </a>
      )}
    </section>
  );
}
