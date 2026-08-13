"""Внутригородские округа и районы — алиасы своего города.

    ingest/.venv/bin/python scripts/add_city_district_aliases.py

Полноценными зонами внутригородское деление не делается намеренно: на
~90 тысяч сообщений корпуса таких упоминаний считаные единицы, честных
полигонов в наших источниках нет, а сетка районов внутри города на карте
страны — шум. Но сообщение «Прикубанский округ, опасность по БПЛА» без
слова «Краснодар» должно попадать в Краснодар, а не теряться, — для
этого достаточно имени в zone_names.

Берутся только имена, которых в справочнике нет вовсе: «Кировский
район» есть в трёх субъектах настоящими районами, и трогать его нельзя.
Скрипт идемпотентен: повторный запуск ничего не дублирует.
"""

from __future__ import annotations

import sys
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.db import DB_PATH, connect
from pipeline.textnorm import norm_key

# Город (id зоны в справочнике) -> его внутригородские округа и районы.
# Только различимые имена: всё, что совпадает с настоящими районами
# других субъектов, отсеет проверка по zone_names ниже — но очевидные
# тёзки («Ленинский», «Октябрьский») сюда даже не вписаны.
# Ловушка, в которую легко попасть: «Ворошиловский район» есть и в
# Ростове, и в Волгограде, «Фрунзенский» — в Саратове, Ярославле и
# Петербурге. Алиас двусмысленного имени уводил бы чужие сообщения в
# один город, поэтому здесь только имена, существующие в одном городе
# страны. Проверка по zone_names ловит тёзок с настоящими районами
# субъектов, но межгородскую двусмысленность она не видит — её держит
# этот список.
CITY_DISTRICTS: dict[str, list[str]] = {
    "gorodskoy_okrug_krasnodar_krasnodarskiy_kray": [
        "Прикубанский округ", "Карасунский округ",
    ],
    "voronezh_voronezhskaya_oblast": [
        "Коминтерновский район",
    ],
    "kursk_kurskaya_oblast": [
        "Сеймский округ", "Железнодорожный округ",
    ],
    # Опечатка одной ленты, но устойчивая: «Адлеровский» вместо
    # «Адлерский». Место не находилось, и сообщение о сбитии над Адлером
    # садилось на весь Краснодарский край — точечное событие красило
    # субъект целиком.
    "adler_sochi_krasnodarskiy_kray": [
        "Адлеровский район",
    ],
}


def main() -> int:
    added = 0
    with closing(connect()) as connection:
        for city_id, districts in CITY_DISTRICTS.items():
            if not connection.execute(
                    "SELECT 1 FROM zones WHERE id = ?", (city_id,)).fetchone():
                print(f"города нет в справочнике, пропуск: {city_id}")
                continue
            for name in districts:
                key = norm_key(name)
                taken = connection.execute(
                    "SELECT zone_id FROM zone_names WHERE norm = ?",
                    (key,)).fetchall()
                # Имя уже занято настоящей зоной (в т.ч. районом другого
                # субъекта) — алиас создал бы тёзку-ловушку. Не трогаем.
                if any(row["zone_id"] != city_id for row in taken):
                    print(f"занято, пропуск: {name}")
                    continue
                if not taken:
                    connection.execute(
                        "INSERT INTO zone_names (norm, zone_id, kind)"
                        " VALUES (?,?,?)", (key, city_id, "variant"))
                    added += 1
                    print(f"алиас: {name} -> {city_id}")
        connection.commit()
    print(f"итого добавлено: {added} (база {DB_PATH})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
