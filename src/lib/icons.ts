/**
 * Значки угроз для карты.
 *
 * Нарисованы здесь, а не взяты у существующих сервисов: у их ассетов нет
 * подтверждённой лицензии, а репозиторий публичный. Инлайновый SVG заодно
 * избавляет от бинарных файлов и позволяет красить значок под уровень
 * опасности прямо в рантайме.
 */

export type IconKind =
  | "uav"
  | "fpv"
  | "rocket"
  | "kab"
  | "bek"
  | "aviation"
  | "intercept"
  | "impact"
  | "allclear"
  | "unknown";

/** Глифы в системе координат 32x32, рисуются белым поверх цветного круга. */
const GLYPHS: Record<IconKind, string> = {
  // Ударный дальнобойный дрон, вид сверху: треугольное крыло, два киля,
  // толкающий винт за хвостом. Здесь стоял квадрокоптер, и это была не
  // придирка к рисунку, а неверное сообщение: вглубь страны идут «Герань»
  // и подобные аппараты самолётной схемы с дальностью в сотни километров,
  // а квадрокоптер на карте обещал совсем другую угрозу — коптер работает
  // в считанных километрах от линии соприкосновения. Мультикоптер остался
  // там, где ему и место: на значке FPV.
  uav: `
    <path d="M16 5.4 L26.6 20.6 L18.1 20.6 L18.1 24.6 L13.9 24.6 L13.9 20.6 L5.4 20.6 Z" fill="#fff"/>
    <path d="M12.4 26.6 L19.6 26.6" stroke="#fff" stroke-width="1.7" stroke-linecap="round"/>`,

  // FPV: мультикоптер с камерой. Аппарат ближнего боя, работает у самой
  // линии соприкосновения, поэтому в сводках вглубь страны почти не идёт.
  fpv: `
    <path d="M11.5 12 L20.5 20 M20.5 12 L11.5 20" stroke="#fff" stroke-width="1.9" stroke-linecap="round"/>
    <circle cx="11" cy="11.5" r="2.6" fill="none" stroke="#fff" stroke-width="1.6"/>
    <circle cx="21" cy="11.5" r="2.6" fill="none" stroke="#fff" stroke-width="1.6"/>
    <circle cx="11" cy="20.5" r="2.6" fill="none" stroke="#fff" stroke-width="1.6"/>
    <circle cx="21" cy="20.5" r="2.6" fill="none" stroke="#fff" stroke-width="1.6"/>
    <path d="M14 14 L18 14 L21 16 L18 18 L14 18 Z" fill="#fff"/>`,

  // Ракета: корпус, носовой обтекатель, стабилизаторы.
  rocket: `
    <path d="M16 5 C18.6 8.4 19.9 12 19.9 16 L19.9 21.5 L12.1 21.5 L12.1 16
             C12.1 12 13.4 8.4 16 5 Z" fill="#fff"/>
    <path d="M12.1 17 L8.6 22.5 L12.1 21.6 Z M19.9 17 L23.4 22.5 L19.9 21.6 Z" fill="#fff"/>
    <path d="M13.6 21.5 L13.6 25 L16 27.5 L18.4 25 L18.4 21.5 Z" fill="#fff"/>`,

  // КАБ: бомба с хвостовым оперением.
  kab: `
    <path d="M16 5 C19 8 20.4 11.4 20.4 15.4 C20.4 18.6 18.4 21 16 22.6
             C13.6 21 11.6 18.6 11.6 15.4 C11.6 11.4 13 8 16 5 Z" fill="#fff"/>
    <path d="M11.6 22 L8.4 27 L13 24.6 Z M20.4 22 L23.6 27 L19 24.6 Z" fill="#fff"/>
    <path d="M14.4 23 L16 27.6 L17.6 23 Z" fill="#fff"/>`,

  // БЭК: безэкипажный катер — корпус и волна.
  bek: `
    <path d="M7.5 17.5 L24.5 17.5 L21.5 22 L10.5 22 Z" fill="#fff"/>
    <path d="M15 8.5 L17.5 8.5 L17.5 17 L15 17 Z" fill="#fff"/>
    <path d="M17.5 9.5 L22.5 13 L17.5 14.6 Z" fill="#fff"/>
    <path d="M7 24.5 Q10 23 13 24.5 T19 24.5 T25 24.5" fill="none" stroke="#fff"
          stroke-width="1.7" stroke-linecap="round"/>`,

  // Авиация: самолёт сверху.
  aviation: `
    <path d="M16 4.5 C17.2 6.2 17.7 8 17.7 10.2 L17.7 13.3 L27 19 L27 21.6
             L17.7 18.7 L17.7 24.3 L21 26.9 L21 28.6 L16 27 L11 28.6 L11 26.9
             L14.3 24.3 L14.3 18.7 L5 21.6 L5 19 L14.3 13.3 L14.3 10.2
             C14.3 8 14.8 6.2 16 4.5 Z" fill="#fff"/>`,

  // Работа ПВО: вспышка перехвата.
  intercept: `
    <path d="M16 4.5 L18 13 L26 8.5 L21.5 16.5 L28.5 18.5 L20 20.5 L23.5 28
             L16 22.5 L8.5 28 L12 20.5 L3.5 18.5 L10.5 16.5 L6 8.5 L14 13 Z" fill="#fff"/>
    <circle cx="16" cy="17" r="3.1" fill="none" stroke="#fff" stroke-width="1.5"/>`,

  // Взрыв: звезда попадания.
  impact: `
    <path d="M16 3.5 L19.2 12.2 L27.5 8.6 L23 16.4 L29 19.4 L20.6 20.8
             L22.4 29 L16 23.6 L9.6 29 L11.4 20.8 L3 19.4 L9 16.4
             L4.5 8.6 L12.8 12.2 Z" fill="#fff"/>`,

  // Отбой: галочка.
  allclear: `
    <path d="M9 16.5 L14 21.5 L23.5 11" fill="none" stroke="#fff" stroke-width="3.4"
          stroke-linecap="round" stroke-linejoin="round"/>`,

  // Неизвестно: восклицательный знак.
  unknown: `
    <rect x="14.4" y="8" width="3.2" height="11.5" rx="1.6" fill="#fff"/>
    <circle cx="16" cy="23.5" r="2.1" fill="#fff"/>`
};

/** Круглая подложка со значком, готовая для ol/style/Icon. */
export function threatIcon(kind: IconKind, color: string, opacity = 1): string {
  const glyph = GLYPHS[kind] ?? GLYPHS.unknown;
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="34" height="34" viewBox="0 0 32 32">
    <circle cx="16" cy="16" r="15" fill="#ffffff" opacity="${(opacity * 0.92).toFixed(2)}"/>
    <circle cx="16" cy="16" r="13" fill="${color}" opacity="${opacity.toFixed(2)}"/>
    <g opacity="${opacity.toFixed(2)}">${glyph}</g>
  </svg>`;
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg.replace(/\s+/g, " "))}`;
}

import { fadeWindow, ZONE_FADE_FLOOR } from "./paint";

/**
 * Насколько выцвел значок к моменту просмотра.
 *
 * Та же физика, что у заливки: окно пролёта района, поправка на скорость
 * угрозы, тот же пол. Раньше у значка был свой срок в 30 минут с полом 0.2,
 * и кривая упиралась в пол уже к двадцати минутам — двадцатиминутная
 * фиксация горела так же, как двухчасовая, и разница возрастов на карте
 * не читалась вовсе.
 *
 * Окно всегда районное, даже если зона события — посёлок или субъект:
 * значок точечный, его масштаб — окрестность точки.
 */
export function iconFreshness(ageMs: number, threat?: string): number {
  const share = Math.min(1, Math.max(0, ageMs) / fadeWindow("district", threat));
  return Math.max(ZONE_FADE_FLOOR, 1 - share ** 0.5);
}

/**
 * Стрелка курса, рисуется отдельным значком поверх круга.
 *
 * Холст больше круга, шеврон у верхней кромки: вращение вокруг центра
 * (anchor 0.5) выносит его на нужную сторону, за край круглой подложки.
 * Ленты часто пишут, откуда идёт борт («с юго-запада»), — стрелка
 * показывает, куда он идёт дальше, и карта отвечает на вопрос «на нас?».
 */
export function directionArrow(color: string, opacity = 1): string {
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="56" height="56" viewBox="0 0 56 56">
    <path d="M28 2.5 L34 12 L28 9 L22 12 Z" fill="${color}" opacity="${opacity.toFixed(2)}"
          stroke="#0b0d0d" stroke-width="1" stroke-linejoin="round"/>
  </svg>`;
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg.replace(/\s+/g, " "))}`;
}

/**
 * Какой значок ставить.
 *
 * Сигнал важнее типа угрозы: сбитие и взрыв — это про исход, а не про то,
 * чем именно летели.
 */
export function iconKindFor(signalType: string, threatType: string): IconKind {
  if (signalType === "allclear") return "allclear";
  if (signalType === "impact") return "impact";
  if (signalType === "intercept") return "intercept";

  switch (threatType) {
    case "uav":
      return "uav";
    case "fpv":
      return "fpv";
    case "rocket":
      return "rocket";
    case "kab":
      return "kab";
    case "bek":
      return "bek";
    case "aviation":
      return "aviation";
    default:
      return "unknown";
  }
}

/** Сигналы, у которых есть конкретная точка, а не площадь. */
const POINT_SIGNALS = new Set(["detection", "intercept", "impact", "allclear"]);

/**
 * Ставить ли на карту значок.
 *
 * Значок означает «здесь». Координаты события — центр его зоны, и для НП или
 * района это честно: место названо, размер его невелик. Для оповещения по
 * целой области центр — точка случайная: она попадает в какой-нибудь тихий
 * район, человек нажимает именно туда и получает «сообщений нет». Треть
 * значков на карте стояла так. Область показывает заливка, она для того и
 * нужна; значок ей не помощник, а помеха.
 */
export function isPointEvent(signalType: string, zoneLevel?: string): boolean {
  if (zoneLevel === "region") return false;
  return POINT_SIGNALS.has(signalType);
}
