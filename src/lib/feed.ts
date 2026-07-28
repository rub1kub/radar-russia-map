/**
 * Что показать в ленте, когда на карте выбрано место.
 *
 * Человек нажимает на район, чтобы понять, почему он закрашен. Ответ «в
 * районе сообщений нет» на этот вопрос не отвечает, а лента со всей страны
 * отвечает неправильно: в кадре был соседний субъект, и под тихим районом
 * Ростовской области показывались тревоги Краснодарского края.
 */

import type { RadarEvent } from "./api";

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
