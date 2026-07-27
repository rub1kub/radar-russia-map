/**
 * Форматирование для интерфейса.
 *
 * Всё время приходит из API в UTC со смещением, а показывается по Москве
 * и подписывается. Раньше метка резалась срезом строки и молча показывала UTC.
 */

const MSK_TIME = new Intl.DateTimeFormat("ru-RU", {
  timeZone: "Europe/Moscow",
  hour: "2-digit",
  minute: "2-digit"
});

const MSK_DAY = new Intl.DateTimeFormat("ru-RU", {
  timeZone: "Europe/Moscow",
  day: "2-digit",
  month: "2-digit"
});

export const numberFormat = new Intl.NumberFormat("ru-RU");

/** Русское склонление по числу: 1 сообщение, 2 сообщения, 5 сообщений. */
export function plural(count: number, one: string, few: string, many: string): string {
  const mod100 = Math.abs(count) % 100;
  const mod10 = Math.abs(count) % 10;
  if (mod100 >= 11 && mod100 <= 14) return many;
  if (mod10 === 1) return one;
  if (mod10 >= 2 && mod10 <= 4) return few;
  return many;
}

/** Время события. Дата добавляется, только если это не сегодня. */
export function formatMoment(iso: string, nowIso: string): string {
  const moment = new Date(iso);
  const time = MSK_TIME.format(moment);
  return MSK_DAY.format(moment) === MSK_DAY.format(new Date(nowIso))
    ? time
    : `${MSK_DAY.format(moment)} ${time}`;
}

export function formatDayTime(iso: string): string {
  const moment = new Date(iso);
  return `${MSK_DAY.format(moment)} ${MSK_TIME.format(moment)}`;
}

/** Сколько минут длилось событие. Ноль означает единственный момент. */
export function durationMinutes(fromIso: string, toIso: string): number {
  return Math.max(
    0,
    Math.round((new Date(toIso).getTime() - new Date(fromIso).getTime()) / 60000)
  );
}

/** Сколько длится событие. Человеку это важнее момента последнего сообщения. */
export function formatDuration(fromIso: string, toIso: string): string {
  const minutes = Math.max(
    0,
    Math.round((new Date(toIso).getTime() - new Date(fromIso).getTime()) / 60000)
  );
  if (minutes < 1) return "только что";
  if (minutes < 60) return `${minutes} ${plural(minutes, "минуту", "минуты", "минут")}`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  const head = `${hours} ${plural(hours, "час", "часа", "часов")}`;
  return rest ? `${head} ${rest} мин` : head;
}

/** «12 минут назад» — сколько прошло с последнего подтверждения. */
export function formatSince(iso: string, nowIso: string): string {
  const minutes = Math.max(
    0,
    Math.round((new Date(nowIso).getTime() - new Date(iso).getTime()) / 60000)
  );
  if (minutes < 1) return "только что";
  if (minutes < 60) return `${minutes} ${plural(minutes, "минуту", "минуты", "минут")} назад`;
  const hours = Math.round(minutes / 60);
  return `${hours} ${plural(hours, "час", "часа", "часов")} назад`;
}

/** Возраст данных для баннера об остановке сбора. */
export function formatAge(seconds: number): string {
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} ${plural(minutes, "минуту", "минуты", "минут")}`;
  const hours = Math.round(minutes / 60);
  return `${hours} ${plural(hours, "час", "часа", "часов")}`;
}

export function formatCount(count: number, one: string, few: string, many: string): string {
  return `${numberFormat.format(count)} ${plural(count, one, few, many)}`;
}

/** Цвет по уровню опасности. Намеренно разведён с цветами подложки. */
export function severityColor(severity: number, alpha: number): string {
  if (severity >= 8) return `rgba(233, 62, 78, ${alpha})`;
  if (severity >= 6) return `rgba(247, 129, 43, ${alpha})`;
  if (severity >= 4) return `rgba(246, 199, 61, ${alpha})`;
  return `rgba(126, 160, 214, ${alpha})`;
}

export const SIGNAL_LABELS: Record<string, string> = {
  danger: "Опасность",
  alarm: "Тревога",
  detection: "Фиксация",
  intercept: "Работа ПВО",
  impact: "Взрыв",
  caution: "Меры безопасности",
  infra: "Инфраструктура",
  allclear: "Отбой",
  retracted: "Опровержение"
};

export const THREAT_LABELS: Record<string, string> = {
  uav: "БПЛА",
  fpv: "FPV",
  rocket: "Ракета",
  kab: "КАБ/УАБ",
  bek: "БЭК",
  aviation: "Авиация",
  unknown: "Неизвестно"
};

export function signalLabel(signal: string): string {
  return SIGNAL_LABELS[signal] ?? signal;
}

export function threatLabel(threat: string): string {
  return THREAT_LABELS[threat] ?? threat;
}
