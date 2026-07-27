import { useEffect, useRef } from "react";
import { Clock, Pause, Play, Radio } from "lucide-react";
import type { HistoryDay } from "../lib/api";
import { formatDayTime, plural, severityColor } from "../lib/format";
import type { Slot } from "../lib/history";

type Props = {
  open: boolean;
  slots: Slot[];
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
                      height: `${Math.max(8, entry.density * 100)}%`,
                      background: severityColor(entry.max_severity, 0.75)
                    }}
                    aria-hidden="true"
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
