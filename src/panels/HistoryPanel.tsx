import { useEffect, useRef, useState } from "react";
import { Clock, Pause, Play, Radio, X } from "lucide-react";
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
 * Куда прижимать подсказку дня.
 *
 * По центру столбика — только в середине полосы. У крайних дней половина
 * плашки уезжала за панель прямо на карту, а обрезать её нельзя: там дата.
 */
function tipAlign(index: number, total: number): "left" | "center" | "right" {
  const share = total > 1 ? index / (total - 1) : 0.5;
  if (share < 0.25) return "left";
  if (share > 0.75) return "right";
  return "center";
}

function tipOffset(index: number, total: number): React.CSSProperties {
  const align = tipAlign(index, total);
  if (align === "left") return { left: 0 };
  if (align === "right") return { right: 0 };
  return { left: `${(index / Math.max(1, total - 1)) * 100}%` };
}

const MONTHS = ["янв", "фев", "мар", "апр", "мая", "июн",
                "июл", "авг", "сен", "окт", "ноя", "дек"];

/**
 * Цвет столбика по напряжённости часа.
 *
 * Прежде диаграмма красилась максимальным уровнем среза, и в горячие сутки
 * все столбики были одинаково красными: понять по ней, где было тише, а где
 * гуще, было нельзя. Теперь цвет говорит про плотность — спокойно, средне,
 * плотно, — а уровень опасности и так виден на самой карте.
 */
function loadColor(value: number, peak: number, dim: boolean): string {
  // Доля берётся напрямую, а не через корень: корень нужен высоте, чтобы
  // спокойные часы было видно, а цвету он всё сдвигает вверх — половина
  // суток красная там, где на деле треть от пика.
  const share = peak > 0 ? value / peak : 0;
  const alpha = dim ? 0.55 : 0.95;
  if (share >= 0.75) return `rgba(233, 62, 78, ${alpha})`;
  if (share >= 0.5) return `rgba(247, 129, 43, ${alpha})`;
  if (share >= 0.25) return `rgba(246, 199, 61, ${alpha})`;
  return `rgba(124, 191, 142, ${alpha})`;
}

/** Отметки времени под диаграммой: начало, четверти, конец. */
function timeTicks(slots: Slot[]): Array<{ at: number; label: string }> {
  if (slots.length < 2) return [];
  const marks = [0, 0.25, 0.5, 0.75, 1];
  const seen = new Set<string>();
  const out: Array<{ at: number; label: string }> = [];
  for (const mark of marks) {
    const index = Math.min(slots.length - 1, Math.round(mark * (slots.length - 1)));
    const moment = new Date(slots[index].at);
    const label = new Intl.DateTimeFormat("ru-RU", {
      timeZone: "Europe/Moscow",
      hour: "2-digit",
      minute: "2-digit"
    }).format(moment);
    if (seen.has(label)) continue;
    seen.add(label);
    out.push({ at: mark, label });
  }
  return out;
}

/**
 * Отметки месяцев под полосой дней: подписываем там, где месяц сменился.
 *
 * Соседние подписи разводятся по ширине: после того как из полосы убрали
 * дни без сбора, апрель и июль встали вплотную, и «апр» с «июл» слиплись
 * в нечитаемое «икатр».
 */
const TICK_GAP = 0.12;

function monthTicks(days: HistoryDay[]): Array<{ at: number; label: string }> {
  const out: Array<{ at: number; label: string }> = [];
  let previous = "";
  let lastAt = -1;
  days.forEach((entry, index) => {
    const month = entry.day.slice(5, 7);
    if (month === previous) return;
    previous = month;
    const at = days.length > 1 ? index / (days.length - 1) : 0;
    // Слиплись — оставляем поздний: он занимает полосу дальше вправо, а
    // ранний это хвост в один-два дня.
    if (lastAt >= 0 && at - lastAt < TICK_GAP) out.pop();
    lastAt = at;
    out.push({ at, label: MONTHS[Number(month) - 1] ?? month });
  });
  return out;
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
  // Подсказка дня ведётся отсюда, а не через data-tip: внутри полосы с
  // overflow-x CSS-подсказку обрезало, а вынесенная в фиксированный угол
  // отрывалась от столбика и ложилась поверх диаграммы.
  const [hoverDay, setHoverDay] = useState<number | null>(null);

  useEffect(() => {
    if (!playing || !slots.length) return;
    timer.current = window.setInterval(() => {
      onSeek(index + 1 >= slots.length ? 0 : index + 1);
    }, 700 / speed);
    return () => {
      if (timer.current !== null) window.clearInterval(timer.current);
    };
  }, [playing, index, slots.length, speed, onSeek]);

  // Стрелками мотать удобнее, чем тянуть ползунок: шаг ровно в один срез.
  // Пробел — пуск и пауза, как в любом плеере.
  useEffect(() => {
    if (!open || !slots.length) return;
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target && /^(INPUT|TEXTAREA)$/.test(target.tagName)) return;
      if (event.key === "ArrowLeft") onSeek(Math.max(0, index - 1));
      else if (event.key === "ArrowRight") onSeek(Math.min(slots.length - 1, index + 1));
      else if (event.key === " ") {
        event.preventDefault();
        onTogglePlay();
      } else return;
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, slots.length, index, onSeek, onTogglePlay]);

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

      {/* Явный выход. Заголовок тоже закрывает, но кнопкой он не выглядит,
          и рука ищет крестик там же, где он у всех остальных панелей. */}
      {open ? (
        <button
          className="history-close"
          type="button"
          onClick={onToggleOpen}
          aria-label="Закрыть историю"
        >
          <X size={15} aria-hidden="true" />
        </button>
      ) : null}

      {open ? (
        <div className="history-body">
          {days.length ? (
            <>
            <div className="day-strip-wrap">
              <div
                className="day-strip"
                role="group"
                aria-label="Выбор дня"
                onMouseLeave={() => setHoverDay(null)}
              >
                {days.map((entry, index) => (
                  <button
                    key={entry.day}
                    type="button"
                    className={`day-bar ${selectedDay === entry.day ? "is-on" : ""}`}
                    onClick={() => onPickDay(selectedDay === entry.day ? null : entry.day)}
                    onMouseEnter={() => setHoverDay(index)}
                    onFocus={() => setHoverDay(index)}
                    onBlur={() => setHoverDay(null)}
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
              {/* Подсказка стоит под своим столбиком и едет вместе с ним.
                  У крайних дней центрировать её нельзя — половина уезжает
                  за панель; там она прижимается к краю. */}
              {hoverDay !== null && days[hoverDay] ? (
                <div
                  className={`day-tip is-${tipAlign(hoverDay, days.length)}`}
                  style={tipOffset(hoverDay, days.length)}
                  role="tooltip"
                >
                  <b>{dayLabel(days[hoverDay].day)}</b> ·{" "}
                  {days[hoverDay].events}{" "}
                  {plural(days[hoverDay].events, "событие", "события", "событий")},
                  подтверждено {days[hoverDay].confirmed}
                </div>
              ) : null}
            </div>
            {/* Полоса тянется на месяцы, и без подписи непонятно, куда
                вообще мотаешь. Месяц подписывается там, где сменился. */}
            <div className="strip-axis" aria-hidden="true">
              {monthTicks(days).map((tick) => (
                <span key={tick.label} style={{ left: `${tick.at * 100}%` }}>
                  {tick.label}
                </span>
              ))}
            </div>
            </>
          ) : null}

          {slots.length > 1 && load.length === slots.length ? (
            <>
            <div className="history-chart">
              {load.map((point, at) => (
                <button
                  key={slots[at].at}
                  type="button"
                  className={`chart-bar ${at === index ? "is-on" : ""}`}
                  onClick={() => onSeek(at)}
                  tabIndex={-1}
                  data-tip={`${formatDayTime(slots[at].at)} · ${point.count} ${plural(
                    point.count,
                    "событие",
                    "события",
                    "событий"
                  )}`}
                  aria-label={`${formatDayTime(slots[at].at)}, событий ${point.count}`}
                >
                  <i
                    style={{
                      height: `${barHeight(point.count, chartPeak)}%`,
                      background: loadColor(point.count, chartPeak, at !== index)
                    }}
                  />
                </button>
              ))}
            </div>
            {/* Ось времени: без неё ползунок мотает в пустоту, и понять,
                какой это час, можно было только по метке внизу. */}
            <div className="chart-axis" aria-hidden="true">
              {timeTicks(slots).map((tick) => (
                <span key={tick.label} style={{ left: `${tick.at * 100}%` }}>
                  {tick.label}
                </span>
              ))}
            </div>
            </>
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
                  aria-label="Момент времени. Стрелки — шаг, пробел — пуск"
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
