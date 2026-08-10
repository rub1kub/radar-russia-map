/**
 * Свой регион — тот, на котором карта открывается по умолчанию.
 *
 * Жители возвращаются сюда десятки раз в день и каждый раз заново искали
 * свою область: закладка с «?region=» есть не у всех, а обзор страны на
 * телефоне — это ещё два жеста. Регион запоминается сам при выборе и
 * забывается, когда выделение сняли.
 *
 * Хранится только в браузере: это предпочтение, а не учётная запись.
 */

const STORAGE_KEY = "radar.region";

export function loadHomeRegion(): string | null {
  try {
    return window.localStorage.getItem(STORAGE_KEY);
  } catch {
    // Приватный режим — стартуем с обзора страны.
    return null;
  }
}

/** Запомнить регион как свой; null — забыть. */
export function rememberHomeRegion(zone: string | null): void {
  try {
    if (zone) window.localStorage.setItem(STORAGE_KEY, zone);
    else window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    // Переполнение или запрет записи не должны ломать карту.
  }
}

/**
 * Какой регион открыть на старте.
 *
 * Адрес важнее памяти: по ссылке из ленты, из бота или с посадочной
 * страницы ждут именно названный регион, даже если в прошлый раз смотрели
 * другой. В адресе регион записан слагом с дефисами («kurskaya-oblast»), в
 * справочнике зона — с подчёркиваниями.
 */
export function pickStartRegion(
  urlParam: string | null,
  stored: string | null
): string | null {
  if (urlParam) return urlParam.replace(/-/g, "_");
  return stored || null;
}
