import { useEffect, useRef } from "react";
import { Clock, Pause, Play, Radio } from "lucide-react";
import type { HistoryDay } from "../lib/api";
import { formatDayTime, plural, severityColor } from "../lib/format";
import type { Slot } from "../lib/history";

export type SlotLoad = { count: number; severity: number };

type Props = {
  open: boolean;
  slots: Slot[];
  /** Сколько событий и какого уровня было в каждом срезе. */
  load: SlotLoad[];
  index: number;
  playing: boolean;
  loading: boolean;
  days: HistoryDay[];
  selectedDay: string | null;
  speed: number;
  onToggleOpen: () => void;
  onSeek: (index: number) => void;
  onTogglePlay: () => void;
  onLive: () => void;
  onPickDay: (day: string | null) => void;
  onSpeed: (speed: number) => void;
};

const SPEEDS = [0.5, 1, 2, 4];

/**
 * Высота столбика внутри просматриваемого окна.
 *
 * Корень, а не доля от пика: внутри суток счёт меняется в разы, и корень
 * поднимает спокойные часы над самым низом, не сплющивая всплеск.
 */
function barHeight(value: number, peak: number): number {
  if (!value) return 0;
  return Math.max(9, Math.round((Math.sqrt(value) / Math.sqrt(peak)) * 100));
}

/**
 * Высота столбика по дням — шкала логарифмическая.
 *
 * Здесь разброс на три порядка: в спокойные сутки событий единицы, в налёт
 * полторы тысячи. И при доле от пика, и при корне весь месяц оставался
 * одинаковыми точками у самого низа — полоса выглядела пунктиром и не
 * говорила ничего. Логарифм разводит единицы и десятки, а пик оставляет
 * пиком.
 */
function dayHeight(value: number, peak: number): number {
  if (!value) return 0;
  return Math.max(12, Math.round((Math.log1p(value) / Math.log1p(peak)) * 100));
}

function dayLabel(day: string): string {
  const [, month, date] = day.split("-");
  return `${date}.${month}`;
}

/**
 * Плеер истории.
 *
 * Срезы внутри суток считаются на клиенте из одной выгрузки, поэтому
 * перемотка мгновенная. Полоски по дням показывают, где вообще есть что
 * смотреть: корпус растянут на месяцы, но плотность крайне неровная, и без
 * подсказки человек перематывал бы пустоту.
 */
export function HistoryPanel({
  open,
  slots,
  load,
  index,
  playing,
  loading,
  days,
  selectedDay,
  speed,
  onToggleOpen,
  onSeek,
  onTogglePlay,
  onLive,
  onPickDay,
  onSpeed
}: Props) {
  const timer = useRef<number | null>(null);
  // Пик по дням, чтобы полоски считались той же шкалой, что и диаграмма.
  const daysPeak = Math.max(1, ...days.map((entry) => entry.events));

  useEffect(() => {
    if (!playing || !slots.length) return;
    timer.current = window.setInterval(() => {
      onSeek(index + 1 >= slots.length ? 0 : index + 1);
    }, 700 / speed);
    return () => {
      if (timer.current !== null) window.clearInterval(timer.current);
    };
  }, [playing, index, slots.length, speed, onSeek]);

  const atLive = !selectedDay && index >= slots.length - 1;
  const current = slots[index];
  const chartPeak = Math.max(1, ...load.map((point) => point.count));
  const now = load[index];

  return (
    <div className={`history-panel ${open ? "is-open" : ""}`}>
      <button className="history-toggle" type="button" onClick={onToggleOpen}>
        <Clock size={16} aria-hidden="true" />
        <span>История</span>
      </button>

      {open ? (
        <div className="history-body">
          {days.length ? (
            <div className="day-strip" role="group" aria-label="Выбор дня">
              {days.map((entry) => (
                <button
                  key={entry.day}
                  type="button"
                  className={`day-bar ${selectedDay === entry.day ? "is-on" : ""}`}
                  onClick={() => onPickDay(selectedDay === entry.day ? null : entry.day)}
                  data-tip={`${dayLabel(entry.day)}: ${entry.events} ${plural(
                    entry.events,
                    "событие",
                    "события",
                    "событий"
                  )}, подтверждено ${entry.confirmed}`}
                  aria-label={`${entry.day}, событий ${entry.events}`}
                >
                  <i
                    style={{
                      height: `${dayHeight(entry.events, daysPeak)}%`,
                      background: severityColor(entry.max_severity, 0.75)
                    }}
                    aria-hidden="true"
                  />
                </button>
              ))}
            </div>
          ) : null}

          {slots.length > 1 && load.length === slots.length ? (
            <div className="history-chart" aria-hidden="true">
              {load.map((point, at) => (
                <button
                  key={slots[at].at}
                  type="button"
                  className={`chart-bar ${at === index ? "is-on" : ""}`}
                  onClick={() => onSeek(at)}
                  tabIndex={-1}
                >
                  <i
                    style={{
                      height: `${barHeight(point.count, chartPeak)}%`,
                      background: severityColor(point.severity, at === index ? 1 : 0.62)
                    }}
                  />
                </button>
              ))}
            </div>
          ) : null}

          {loading ? (
            <p className="history-loading">Загрузка истории…</p>
          ) : !slots.length ? (
            <p className="history-loading">За этот период данных нет.</p>
          ) : (
            <>
              <div className="history-controls">
                <button
                  type="button"
                  onClick={onTogglePlay}
                  aria-label={playing ? "Пауза" : "Воспроизвести"}
                >
                  {playing ? <Pause size={15} aria-hidden="true" /> : <Play size={15} aria-hidden="true" />}
                </button>

                <input
                  type="range"
                  min={0}
                  max={slots.length - 1}
                  value={index}
                  aria-label="Момент времени"
                  onChange={(event) => onSeek(Number(event.target.value))}
                />

                <button
                  type="button"
                  className={atLive ? "is-live" : undefined}
                  onClick={onLive}
                  data-tip="Вернуться к текущей обстановке"
                  aria-label="К текущей обстановке"
                >
                  <Radio size={15} aria-hidden="true" />
                </button>
              </div>

              <div className="history-foot">
                <span className="history-stamp">
                  {atLive ? "сейчас" : current ? formatDayTime(current.at) : ""}
                  {now ? (
                    <b>
                      {now.count} {plural(now.count, "событие", "события", "событий")}
                    </b>
                  ) : null}
                </span>
                <span className="history-speed">
                  {SPEEDS.map((value) => (
                    <button
                      key={value}
                      type="button"
                      className={speed === value ? "is-on" : undefined}
                      onClick={() => onSpeed(value)}
                    >
                      {value}×
                    </button>
                  ))}
                </span>
              </div>
            </>
          )}
        </div>
      ) : null}
    </div>
  );
}
