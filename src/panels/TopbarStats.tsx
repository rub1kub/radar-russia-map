import type { RadarEvent } from "../lib/api";
import { formatDate, plural, severityColor } from "../lib/format";

type Props = {
  /** События на показываемый момент: сейчас или срез истории. */
  events: RadarEvent[] | null;
  /** Момент, к которому относится сводка. */
  moment: string | null;
  /** Сколько зон подсвечено на этот же момент. */
  zones: number;
  /** В скольких регионах нет ни одного события — для подсказки. */
  quietRegions: number | null;
  /** Показан архив, а не эфир. */
  historyLabel: string | null;
};

const LEVELS = [
  {
    key: "high",
    min: 8,
    tip: "Борт уже видят: фиксация, работа ПВО, взрыв"
  },
  {
    key: "mid",
    min: 6,
    tip: "Объявлена тревога, но подтверждённой фиксации нет"
  },
  {
    key: "low",
    min: 4,
    tip: "Предупреждение: борт может прилететь"
  }
] as const;

/**
 * Счётчики обстановки в шапке.
 *
 * Шапка была почти пустой: заголовок слева, кнопка справа. Сводка по уровням
 * опасности — то, что человек хочет увидеть, не читая ленту.
 */
export function TopbarStats({ events, zones, quietRegions, historyLabel, moment }: Props) {
  if (!events) return null;

  const counts = LEVELS.map((level, index) => {
    const upper = index === 0 ? Infinity : LEVELS[index - 1].min;
    return {
      ...level,
      value: events.filter(
        (event) => event.severity >= level.min && event.severity < upper
      ).length
    };
  });

  const quiet = events.length === 0;

  return (
    <div className="topbar-stats" aria-label="Сводка обстановки">
      {/* В режиме истории счётчики шли живые: над пустой архивной картой
          висело «77 79 135 в 237 зонах». Скриншот такого экрана
          неотличим от эфира, и это готовый повод для недоразумения. */}
      {historyLabel ? (
        <span className="stat-archive" data-tip="Показан архив, а не текущая обстановка">
          архив · {historyLabel}
        </span>
      ) : moment ? (
        /* Даты на карте не было вовсе — только время. Человек, услышавший
           взрывы ночью, не мог понять, о сегодняшней ночи речь или о
           вчерашней. */
        <span className="stat-date" data-tip="Дата и время московские">
          {formatDate(moment)}
        </span>
      ) : null}
      {quiet ? (
        <span className="stat-quiet" data-tip="За последние часы сообщений об опасности не поступало">
          Активных сообщений нет
        </span>
      ) : (
        <>
          {counts.map((level) => (
            <span
              key={level.key}
              className={`stat-chip ${level.value ? "" : "is-zero"}`}
              data-tip={`${level.tip}. Событий: ${level.value}`}
            >
              <i style={{ background: severityColor(level.min, 0.95) }} aria-hidden="true" />
              {level.value}
            </span>
          ))}
          <span
            className="stat-zones"
            data-tip={`В скольких местах сейчас есть события; одно событие поднимается по цепочке: посёлок, район, область — это три зоны.${
              quietRegions ? ` Спокойно в ${quietRegions} из 89 регионов` : ""
            }`}
          >
            в {zones} {plural(zones, "зоне", "зонах", "зонах")}
          </span>
        </>
      )}
    </div>
  );
}
