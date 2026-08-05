import { describe, expect, it, afterEach, beforeAll } from "vitest";

// Тесты идут в node-окружении, где window отсутствует, а проверяемый код
// без него не имеет смысла. Подставляем глобальный объект вместо того,
// чтобы тащить jsdom ради четырёх проверок.
beforeAll(() => {
  (globalThis as unknown as { window: unknown }).window = globalThis;
});

const { insideTelegram } = await import("./telegram");

/**
 * Скрипт Telegram создаёт window.Telegram.WebApp в любом браузере, поэтому
 * само его наличие ничего не значит. Если поверить объекту, отступ под
 * панель бота получит каждый посетитель сайта.
 */
describe("определение мини-приложения", () => {
  afterEach(() => {
    delete (window as unknown as { Telegram?: unknown }).Telegram;
  });

  const withApp = (app: Record<string, unknown>) => {
    (window as unknown as { Telegram?: unknown }).Telegram = { WebApp: app };
  };

  it("вне Telegram объекта нет — это не мини-приложение", () => {
    expect(insideTelegram()).toBe(false);
  });

  it("объект есть, но платформа неизвестна — обычный браузер", () => {
    withApp({ platform: "unknown", initData: "" });
    expect(insideTelegram()).toBe(false);
  });

  it("названная платформа означает настоящий запуск", () => {
    withApp({ platform: "ios", initData: "" });
    expect(insideTelegram()).toBe(true);
  });

  it("подписанные данные тоже доказывают запуск из Telegram", () => {
    withApp({ platform: "unknown", initData: "query_id=AAH..." });
    expect(insideTelegram()).toBe(true);
  });
});
