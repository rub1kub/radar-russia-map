/**
 * Web Push для «Моих мест»: тревога и отбой догоняют закрытую вкладку.
 *
 * Без учётных записей: серверу уходит только push-подписка браузера и
 * список зон из закладок. Список перезаписывается при каждом изменении
 * закладок, пока пуш включён.
 */

import { API_BASE } from "./api";

const STORAGE_KEY = "radar.push";

export function pushSupported(): boolean {
  return "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
}

export function pushEnabled(): boolean {
  try {
    return localStorage.getItem(STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

function remember(on: boolean): void {
  try {
    localStorage.setItem(STORAGE_KEY, on ? "1" : "0");
  } catch {
    // Приватный режим: доживёт до перезагрузки.
  }
}

function decodeKey(base64url: string): Uint8Array {
  const padded = base64url.padEnd(base64url.length + ((4 - (base64url.length % 4)) % 4), "=");
  const raw = atob(padded.replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(raw, (char) => char.charCodeAt(0));
}

async function subscription(): Promise<PushSubscription | null> {
  const registration = await navigator.serviceWorker.register("/sw.js");
  return registration.pushManager.getSubscription();
}

/** Включить пуш: разрешение, подписка, отправка зон. Бросает при отказе. */
export async function enablePush(zones: string[]): Promise<void> {
  const permission = await Notification.requestPermission();
  if (permission !== "granted") throw new Error("permission denied");

  const registration = await navigator.serviceWorker.register("/sw.js");
  const keyResponse = await fetch(`${API_BASE}/api/v1/push/key`);
  const { key } = (await keyResponse.json()) as { key: string };

  const existing = await registration.pushManager.getSubscription();
  const active =
    existing ??
    (await registration.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: decodeKey(key).buffer as ArrayBuffer
    }));

  await sendZones(active, zones);
  remember(true);
}

/** Обновить список зон на сервере — зовётся при каждом изменении закладок. */
export async function syncPushZones(zones: string[]): Promise<void> {
  if (!pushEnabled() || !pushSupported()) return;
  const active = await subscription();
  if (!active) return;
  await sendZones(active, zones);
}

async function sendZones(active: PushSubscription, zones: string[]): Promise<void> {
  await fetch(`${API_BASE}/api/v1/push/subscribe`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ subscription: active.toJSON(), zones })
  });
}

export async function disablePush(): Promise<void> {
  remember(false);
  if (!pushSupported()) return;
  const active = await subscription();
  if (!active) return;
  await fetch(`${API_BASE}/api/v1/push/unsubscribe`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ endpoint: active.endpoint })
  }).catch(() => undefined);
  await active.unsubscribe().catch(() => undefined);
}
