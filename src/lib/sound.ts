/**
 * Звук тревоги для «Моих мест».
 *
 * Синтезируется на месте, без аудиофайлов: два коротких тона, негромко.
 * Выключен по умолчанию — звук в теме, где речь о налётах, должен быть
 * осознанным выбором, а не сюрпризом. Браузер всё равно не даст звука до
 * первого жеста пользователя, поэтому включение через кнопку — единственный
 * честный путь.
 */

const STORAGE_KEY = "radar.alertSound";

export function soundEnabled(): boolean {
  try {
    return localStorage.getItem(STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

export function setSoundEnabled(on: boolean): void {
  try {
    localStorage.setItem(STORAGE_KEY, on ? "1" : "0");
  } catch {
    // Приватный режим: пусть живёт до перезагрузки.
  }
}

let context: AudioContext | null = null;

function playTones(tones: ReadonlyArray<readonly [number, number]>, volume: number): void {
  try {
    context = context ?? new AudioContext();
    const now = context.currentTime;
    for (const [offset, frequency] of tones) {
      const oscillator = context.createOscillator();
      const gain = context.createGain();
      oscillator.type = "sine";
      oscillator.frequency.value = frequency;
      gain.gain.setValueAtTime(0.0001, now + offset);
      gain.gain.exponentialRampToValueAtTime(volume, now + offset + 0.03);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + offset + 0.2);
      oscillator.connect(gain).connect(context.destination);
      oscillator.start(now + offset);
      oscillator.stop(now + offset + 0.22);
    }
  } catch {
    // Звука нет — тост всё равно всплывёт.
  }
}

/** Два тона, восходящих, полсекунды на всё. */
export function playAlert(): void {
  playTones([
    [0, 660],
    [0.22, 880]
  ], 0.12);
}

/** Отбой: те же два тона, но вниз и тише — хорошая новость не пугает. */
export function playAllClear(): void {
  playTones([
    [0, 784],
    [0.22, 587]
  ], 0.08);
}
