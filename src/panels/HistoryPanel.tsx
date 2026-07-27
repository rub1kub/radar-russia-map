import { useEffect, useRef } from "react";
import { Clock, Pause, Play, Radio } from "lucide-react";
import { formatDayTime } from "../lib/format";
import type { Slot } from "../lib/history";

type Props = {
  open: boolean;
  slots: Slot[];
  index: number;
  playing: boolean;
  loading: boolean;
  onToggleOpen: () => void;
  onSeek: (index: number) => void;
  onTogglePlay: () => void;
  onLive: () => void;
};

/**
 * Плеер суточной истории.
 *
 * Срезы считаются на клиенте из одной выгрузки событий, поэтому перемотка
 * мгновенная и не создаёт нагрузки на сервер.
 */
export function HistoryPanel({
  open,
  slots,
  index,
  playing,
  loading,
  onToggleOpen,
  onSeek,
  onTogglePlay,
  onLive
}: Props) {
  const timer = useRef<number | null>(null);

  useEffect(() => {
    if (!playing || !slots.length) return;

    timer.current = window.setInterval(() => {
      onSeek(index + 1 >= slots.length ? 0 : index + 1);
    }, 700);

    return () => {
      if (timer.current !== null) window.clearInterval(timer.current);
    };
  }, [playing, index, slots.length, onSeek]);

  const atLive = index >= slots.length - 1;
  const current = slots[index];

  return (
    <div className={`history-panel ${open ? "is-open" : ""}`}>
      <button className="history-toggle" type="button" onClick={onToggleOpen}>
        <Clock size={16} aria-hidden="true" />
        <span>История за сутки</span>
      </button>

      {open ? (
        <div className="history-body">
          {loading ? (
            <p className="history-loading">Загрузка истории…</p>
          ) : !slots.length ? (
            <p className="history-loading">За сутки данных нет.</p>
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
                  title="Вернуться к текущей обстановке"
                >
                  <Radio size={15} aria-hidden="true" />
                </button>
              </div>

              <p className="history-stamp">
                {atLive ? "сейчас" : current ? formatDayTime(current.at) : ""}
              </p>
            </>
          )}
        </div>
      ) : null}
    </div>
  );
}
