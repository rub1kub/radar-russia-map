import type { RadarState } from "../lib/api";
import { plural, severityColor } from "../lib/format";

type Props = {
  state: RadarState | null;
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
export function TopbarStats({ state }: Props) {
  if (!state) return null;

  const counts = LEVELS.map((level, index) => {
    const upper = index === 0 ? Infinity : LEVELS[index - 1].min;
    return {
      ...level,
      value: state.events.filter(
        (event) => event.severity >= level.min && event.severity < upper
      ).length
    };
  });

  const quiet = state.active_events === 0;

  return (
    <div className="topbar-stats" aria-label="Сводка обстановки">
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
              data-tip={`${level.tip}. Сейчас: ${level.value}`}
            >
              <i style={{ background: severityColor(level.min, 0.95) }} aria-hidden="true" />
              {level.value}
            </span>
          ))}
          <span
            className="stat-zones"
            data-tip="В скольких районах и регионах сейчас есть активные сообщения"
          >
            в {state.active_zones} {plural(state.active_zones, "зоне", "зонах", "зонах")}
          </span>
        </>
      )}
    </div>
  );
}
