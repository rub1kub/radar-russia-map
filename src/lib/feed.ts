/**
 * Что показать в ленте, когда на карте выбрано место.
 *
 * Человек нажимает на район, чтобы понять, почему он закрашен. Ответ «в
 * районе сообщений нет» на этот вопрос не отвечает, а лента со всей страны
 * отвечает неправильно: в кадре был соседний субъект, и под тихим районом
 * Ростовской области показывались тревоги Краснодарского края.
 */

import type { RadarEvent } from "./api";
import { fadeWindow } from "./paint";

export type ZoneFeed = {
  events: RadarEvent[];
  /** Обстановка взята по области, а не по самому месту. */
  fromRegion: boolean;
};

/**
 * @param zoneId зона выбранного места; null, если о ней ничего не известно —
 *   так бывает всегда, когда в месте тихо: соответствие полигонов зонам
 *   строится из счётчиков обстановки, а там только зоны с событиями.
 * @param regionZoneId зона региона, внутри которого лежит выбранное место.
 */
export function zoneFeed(
  events: RadarEvent[],
  zoneId: string | null,
  regionZoneId: string | null
): ZoneFeed {
  const own = zoneId ? events.filter((event) => event.zone_path.includes(zoneId)) : [];
  if (own.length) return { events: own, fromRegion: false };
  if (!regionZoneId || regionZoneId === zoneId) return { events: [], fromRegion: false };
  return {
    events: events.filter((event) => event.zone_path.includes(regionZoneId)),
    fromRegion: true
  };
}

/**
 * Сколько минут эфир молчит по месту — если молчит значимо долго.
 *
 * Главный вопрос человека под тревогой: «она ещё действует — мне можно
 * выходить?» Половина лент отбоев не пишет, событие просто затухает, и
 * карта на вопрос не отвечала. Отвечаем честно вычислением: когда у всех
 * незакрытых событий вышло окно пролёта (то самое, по которому гаснет
 * заливка — скорость борта на размер зоны), борт зону уже покинул бы.
 *
 * null — вердикта нет: либо всё закрыто отбоем (его карточки и так
 * показывают), либо окно ещё не вышло и тревога в силе.
 */
export function quietMinutes(events: RadarEvent[], referenceIso: string): number | null {
  const open = events.filter((event) => event.status !== "resolved");
  if (!open.length) return null;

  const reference = new Date(referenceIso).getTime();
  let lastMs = 0;
  for (const event of open) {
    const last = new Date(event.last_seen_at).getTime();
    if (reference - last < fadeWindow(event.zone_level, event.threat_type)) return null;
    lastMs = Math.max(lastMs, last);
  }

  const minutes = Math.floor((reference - lastMs) / 60_000);
  return minutes >= 1 ? minutes : null;
}
