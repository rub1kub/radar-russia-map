/**
 * Работа внутри Telegram: карта как мини-приложение.
 *
 * Мини-приложение — это тот же сайт, открытый во встроенном браузере
 * Telegram. Отдельной сборки не нужно, но без правок карта в окне
 * мессенджера ведёт себя как случайно открытая веб-страница, а не как
 * приложение:
 *
 * 1. Окно открывается наполовину высоты — разворачиваем на весь экран.
 * 2. Панели Telegram сверху и снизу светлые по умолчанию и на тёмной карте
 *    выглядят чужеродно — красим в цвет таб-бара, к которому нижняя
 *    примыкает вплотную.
 * 3. Вертикальный свайп закрывает окно, а на карте это жест панорамы.
 * 4. Долгое нажатие выделяет текст и тянет элементы — оба жеста мешают
 *    вести карту пальцем.
 *
 * Всё это включается только внутри Telegram. Проверять наличие объекта
 * Telegram.WebApp мало: скрипт создаёт его в любом браузере, и настоящий
 * запуск выдаёт лишь платформа — см. insideTelegram().
 */

type TelegramWebApp = {
  platform?: string;
  initData?: string;
  initDataUnsafe?: { user?: { allows_write_to_pm?: boolean } };
  version?: string;
  ready: () => void;
  expand: () => void;
  isExpanded?: boolean;
  isFullscreen?: boolean;
  viewportStableHeight?: number;
  setHeaderColor?: (color: string) => void;
  setBackgroundColor?: (color: string) => void;
  setBottomBarColor?: (color: string) => void;
  disableVerticalSwipes?: () => void;
  requestFullscreen?: () => void;
  requestWriteAccess?: (callback?: (granted: boolean) => void) => void;
  onEvent?: (event: string, handler: () => void) => void;
};

declare global {
  interface Window {
    Telegram?: { WebApp?: TelegramWebApp };
  }
}

/**
 * Цвет для панелей мессенджера. Берём из тех же стилей, которыми покрашен
 * таб-бар: панели Telegram стоят к нему вплотную, и любое расхождение
 * читается как шов поперёк экрана. Значение приходится подставлять
 * запасное — на случай, если переменная не объявлена.
 */
function shellColor(): string {
  const value = getComputedStyle(document.documentElement)
    .getPropertyValue("--shell").trim();
  return /^#[0-9a-f]{6}$/i.test(value) ? value : "#0e1211";
}

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

/**
 * Полный экран появился в Bot API 8.0. У старых клиентов метода просто
 * нет — там остаётся развёрнутое окно, и карта работает так же. Поэтому
 * версию не сверяем, а зовём через опциональный доступ.
 */
function goFullscreen(app: TelegramWebApp): void {
  app.expand();
  try {
    app.requestFullscreen?.();
  } catch {
    // Отказ клиента — не повод ломать запуск.
  }
}

export function setupTelegram(): void {
  const app = window.Telegram?.WebApp;
  if (!app || !insideTelegram()) return;

  app.ready();
  goFullscreen(app);

  // Свайп вниз в Telegram закрывает окно. На карте вертикальный жест —
  // это панорама, и приложение схлопывалось прямо во время просмотра.
  app.disableVerticalSwipes?.();

  // Обе панели мессенджера — в цвет таб-бара, иначе поверх тёмной карты
  // висят светлые полосы сверху и снизу, а внизу ещё и шов по границе с
  // нашей навигацией.
  const shell = shellColor();
  app.setHeaderColor?.(shell);
  app.setBackgroundColor?.(shell);
  app.setBottomBarColor?.(shell);

  // Если полный экран не дали, окно всё равно должно быть развёрнутым.
  app.onEvent?.("fullscreenFailed", () => app.expand());

  // Класс включает отступ под панель Telegram и правила поведения жестов.
  document.documentElement.classList.add("in-telegram");

  // Открытие карты — в журнал бота: подписанные данные проверяет сервер,
  // самим им верить нельзя. Ошибка сети здесь никого не касается.
  if (app.initData) {
    import("./api")
      .then(({ API_BASE }) =>
        fetch(`${API_BASE}/api/v1/tg/opened`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ init_data: app.initData })
        }))
      .catch(() => undefined);
  }

  // Право писать человеку: без него бот не может прислать уведомление
  // тому, кто пришёл по ссылке, но ни разу не написал в чат. Telegram
  // помнит выданное разрешение, поэтому спрашиваем только тех, у кого
  // его ещё нет.
  if (app.initDataUnsafe?.user?.allows_write_to_pm === false) {
    try {
      app.requestWriteAccess?.(() => undefined);
    } catch {
      // Старый клиент без метода — просто живём дальше.
    }
  }
}

/**
 * Колокольчик в мини-аппе подписывает на уведомления в сам Telegram.
 *
 * Web Push внутри мессенджера не живёт, а бот — живёт: нажатие на
 * колокольчик уходит боту тем же смыслом, что команда /watch, и бот
 * отвечает в чат подтверждением. Снятие закладки — как /unwatch.
 * Вне Telegram функция молчит: там работают браузерные уведомления.
 */
export function telegramWatch(zoneId: string, on: boolean): void {
  const app = window.Telegram?.WebApp;
  if (!app || !insideTelegram() || !app.initData) return;
  void import("./api")
    .then(({ API_BASE }) =>
      fetch(`${API_BASE}/api/v1/tg/watch`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ init_data: app.initData, zone_id: zoneId, on })
      }))
    .catch(() => undefined);
}
