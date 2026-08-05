/**
 * Работа внутри Telegram: карта как мини-приложение.
 *
 * Мини-приложение — это тот же сайт, открытый во встроенном браузере
 * Telegram. Отдельной сборки не нужно, но три вещи приходится поправить,
 * иначе карта в окне мессенджера выглядит сломанной:
 *
 * 1. Окно открывается наполовину высоты — просим развернуть.
 * 2. Сверху лежит панель Telegram с названием бота и крестиком; без
 *    отступа под неё наша шапка со счётчиками оказывается под ней.
 * 3. У панели свой цвет, по умолчанию светлый — на тёмной карте он режет
 *    глаз, поэтому красим в цвет подложки.
 *
 * Всё это включается только внутри Telegram. Проверять наличие объекта
 * Telegram.WebApp мало: скрипт создаёт его в любом браузере, и настоящий
 * запуск выдаёт лишь платформа — см. insideTelegram().
 */

type TelegramWebApp = {
  platform?: string;
  initData?: string;
  ready: () => void;
  expand: () => void;
  isExpanded?: boolean;
  viewportStableHeight?: number;
  setHeaderColor?: (color: string) => void;
  setBackgroundColor?: (color: string) => void;
  disableVerticalSwipes?: () => void;
};

declare global {
  interface Window {
    Telegram?: { WebApp?: TelegramWebApp };
  }
}

/** Цвет подложки карты. Совпадает с --bg в стилях. */
const SHELL_COLOR = "#0e1211";

export function insideTelegram(): boolean {
  const app = window.Telegram?.WebApp;
  if (!app) return false;
  // Скрипт Telegram создаёт свой объект в любом браузере, а не только в
  // мессенджере: сам по себе он ничего не доказывает. Настоящий запуск
  // выдаёт платформа — вне Telegram она «unknown», и подписанных данных
  // о пользователе тоже нет. Без этой проверки отступ под панель бота
  // получил бы каждый обычный посетитель сайта.
  const platform = app.platform ?? "unknown";
  return platform !== "unknown" || Boolean(app.initData);
}

export function setupTelegram(): void {
  const app = window.Telegram?.WebApp;
  if (!app || !insideTelegram()) return;

  app.ready();
  app.expand();

  // Свайп вниз в Telegram закрывает окно. На карте вертикальный жест —
  // это панорама, и приложение схлопывалось прямо во время просмотра.
  app.disableVerticalSwipes?.();

  app.setHeaderColor?.(SHELL_COLOR);
  app.setBackgroundColor?.(SHELL_COLOR);

  // Класс включает отступ под панель Telegram: высоту она не сообщает, а
  // накрывает верхние 56 px окна.
  document.documentElement.classList.add("in-telegram");
}
