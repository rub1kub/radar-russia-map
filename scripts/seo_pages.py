"""Посадочные страницы регионов — со сводкой по самому региону.

Карта это одностраничное приложение: у неё один адрес и пустой div для
робота, и по запросу «тревога в Белгородской области» поисковику показать
нечего. Отсюда 89 отдельных страниц — по одной на субъект.

Раньше страницы собирал scripts/build-seo-pages.mjs, и они отличались
только названием региона да списком районов. Ровно такую штамповку
поисковики и называют дорвеями: страница, которой не было бы, если бы не
поиск. Поэтому сводка теперь настоящая — сколько сообщений пришло за месяц,
когда было последнее, какие районы называют чаще, в какие часы. Этих цифр
нет больше нигде: они собираются здесь же, из своего корпуса.

Из-за данных генератор переехал на сервер и на Python: база живёт там, и
только там сводка может быть свежей. Запускается после выкатки и по
таймеру:

    python -m scripts.seo_pages          # собрать страницы и sitemap
    python -m scripts.seo_pages --ping   # и позвать роботов через IndexNow
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from contextlib import closing
from datetime import datetime, timedelta
from html import escape
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.db import DB_PATH, ROOT
from pipeline.timeutil import MSK, now_utc

SITE = "https://tihoenebo.com"
OUT = ROOT / "dist"
DATA = ROOT / "public" / "data"

# Окно сводки. Месяц — чтобы попадали и тихие регионы: за неделю у половины
# субъектов нет ничего, и страница снова стала бы пустым шаблоном.
WINDOW = timedelta(days=30)
TOP_DISTRICTS = 6
# Сколько соседей по алфавиту показать в перелинковке. Робот ходит по
# ссылкам, и без них 89 страниц висят каждая сама по себе.
NEIGHBOURS = 6

MONTHS = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля",
          "августа", "сентября", "октября", "ноября", "декабря")

SIGNAL_WORDS = {
    "detection": "фиксации бортов",
    "intercept": "перехваты",
    "impact": "взрывы",
    "alarm": "объявленные тревоги",
    "danger": "предупреждения об опасности",
    "allclear": "отбои",
    "infra": "сообщения об инфраструктуре",
}

THREAT_WORDS = {
    "uav": "беспилотники",
    "fpv": "FPV-дроны",
    "rocket": "ракеты",
    "kab": "управляемые бомбы",
    "bek": "безэкипажные катера",
    "aviation": "авиация",
}


def plural(count: int, one: str, few: str, many: str) -> str:
    mod100, mod10 = abs(count) % 100, abs(count) % 10
    if 11 <= mod100 <= 14:
        return many
    if mod10 == 1:
        return one
    if 2 <= mod10 <= 4:
        return few
    return many


def inflect(name: str) -> str:
    """Предложный падеж субъекта: «в Курской области», а не «в Курская область».

    Именительный в заголовке читается как машинный перевод и сразу выдаёт
    штампованную страницу. Правила идут от частного к общему: «Еврейская
    автономная область» иначе попадает под «...ая область» и теряет середину.
    """
    rules = [
        (r"^Республика (.+)$", r"Республике \1"),
        (r"^Чеченская Республика$", "Чеченской Республике"),
        (r"^(.+)ая автономная область$", r"\1ой автономной области"),
        (r"^(.+)ая народная республика$", r"\1ой народной республике"),
        (r"^(.+)ая республика$", r"\1ой республике"),
        (r"^(.+)ая область$", r"\1ой области"),
        (r"^(.+)ий край$", r"\1ом крае"),
        (r"^(.+)ой край$", r"\1ом крае"),
        (r"^(.+)ий автономный округ(.*)$", r"\1ом автономном округе\2"),
        (r"^(.+)ая$", r"\1ой"),
        (r"^(.+)ия$", r"\1ии"),
        (r"^(.+)я$", r"\1е"),
        (r"^(.+)ань$", r"\1ани"),
        (r"^(.+)а$", r"\1е"),
    ]
    for pattern, repl in rules:
        if re.match(pattern, name, re.IGNORECASE):
            return re.sub(pattern, repl, name, flags=re.IGNORECASE)
    if re.search(r"[ьй]$", name, re.IGNORECASE):
        return re.sub(r"[ьй]$", "е", name, flags=re.IGNORECASE)
    return f"{name}е"


def preposition(name: str) -> str:
    return "во" if name.lower().startswith("вл") else "в"


def moment(iso: str) -> str:
    """«29 июля, 18:26» по Москве — время на карте всегда московское."""
    stamp = datetime.fromisoformat(iso).astimezone(MSK)
    return f"{stamp.day} {MONTHS[stamp.month - 1]}, {stamp:%H:%M}"


def day_word(iso: str) -> str:
    stamp = datetime.fromisoformat(iso).astimezone(MSK)
    return f"{stamp.day} {MONTHS[stamp.month - 1]}"


def load_geo() -> tuple[list[dict], dict[str, list[str]]]:
    regions = json.loads((DATA / "regions.json").read_text(encoding="utf-8"))
    districts = json.loads((DATA / "districts.json").read_text(encoding="utf-8"))
    by_region: dict[str, list[str]] = {}
    for feature in districts["features"]:
        props = feature.get("properties") or {}
        if not props.get("region") or not props.get("name"):
            continue
        by_region.setdefault(props["region"], []).append(props["name"])
    return regions["features"], by_region


def collect_stats(connection: sqlite3.Connection) -> dict[str, dict]:
    """Сводка по каждому региону за окно — одним проходом по событиям.

    Событие поднимается по всей цепочке зон, поэтому регион ищется в
    zone_path: тревога по посёлку считается и региону тоже.
    """
    since = (now_utc() - WINDOW).isoformat()
    rows = connection.execute(
        """
        SELECT e.zone_path, e.zone_id, e.signal_type, e.threat_type,
               e.first_seen_at, z.level, z.name_ru
        FROM events e LEFT JOIN zones z ON z.id = e.zone_id
        WHERE e.first_seen_at >= ?
        """,
        (since,),
    ).fetchall()

    stats: dict[str, dict] = {}
    for row in rows:
        path = json.loads(row["zone_path"] or "[]")
        if not path:
            continue
        # Цепочка идёт снизу вверх: посёлок, район, регион. Регион — последний,
        # и по нему событие засчитывается субъекту целиком.
        region_id = path[-1]
        entry = stats.setdefault(region_id, {
            "events": 0, "days": set(), "last": None,
            "districts": Counter(), "signals": Counter(),
            "threats": Counter(), "hours": Counter(),
        })
        entry["events"] += 1
        stamp = datetime.fromisoformat(row["first_seen_at"]).astimezone(MSK)
        entry["days"].add(stamp.date())
        entry["hours"][stamp.hour] += 1
        if entry["last"] is None or row["first_seen_at"] > entry["last"]:
            entry["last"] = row["first_seen_at"]
        entry["signals"][row["signal_type"]] += 1
        if row["threat_type"] and row["threat_type"] != "unknown":
            entry["threats"][row["threat_type"]] += 1
        # Название района берём у самой мелкой зоны события, но саму область
        # в список не пишем: «чаще всего называют Ростовскую область» на
        # странице Ростовской области — не информация.
        if row["level"] in ("district", "place") and row["name_ru"]:
            entry["districts"][row["name_ru"]] += 1
    return stats


def busiest_window(hours: Counter) -> str | None:
    """Самые шумные шесть часов подряд — «чаще всего с 22 до 4».

    Именно так вопрос и звучит: не «в какой час», а «когда обычно».
    """
    if sum(hours.values()) < 12:
        return None
    best_start, best_sum = 0, -1
    for start in range(24):
        total = sum(hours[(start + offset) % 24] for offset in range(6))
        if total > best_sum:
            best_start, best_sum = start, total
    share = round(best_sum / sum(hours.values()) * 100)
    if share < 35:
        return None
    return f"с {best_start}:00 до {(best_start + 6) % 24}:00 — {share}% всех сообщений"


def summary_block(name: str, stats: dict | None) -> str:
    """Абзацы сводки. Тихий регион — тоже содержание, и его пишем прямо."""
    where = f"{preposition(name)} {inflect(name)}"
    if not stats or not stats["events"]:
        return (
            f"<h2>Что было за последний месяц</h2>\n"
            f"      <p>За последние 30 дней сообщений об опасности {where} в "
            f"отслеживаемых каналах не было. Карта продолжает следить: как "
            f"только что-то появится, регион на ней подсветится, а событие "
            f"встанет в общую ленту со ссылкой на первоисточник.</p>"
        )

    count = stats["events"]
    days = len(stats["days"])
    parts = [
        f"<h2>Что было за последний месяц</h2>",
        f"      <p>За 30 дней карта отметила {where} "
        f"<strong>{count} {plural(count, 'событие', 'события', 'событий')}</strong> "
        f"— в {days} из тридцати {plural(days, 'дня', 'дней', 'дней')}. "
        f"Последнее — {moment(stats['last'])}.</p>",
    ]

    signals = stats["signals"].most_common(3)
    if signals:
        listed = ", ".join(
            f"{SIGNAL_WORDS.get(key, key)} — {value}" for key, value in signals)
        parts.append(f"      <p>Чаще всего это {listed}.</p>")

    threats = stats["threats"].most_common(2)
    if threats:
        listed = ", ".join(
            f"{THREAT_WORDS.get(key, key)} ({value})" for key, value in threats)
        parts.append(f"      <p>Из названного источниками: {listed}.</p>")

    window = busiest_window(stats["hours"])
    if window:
        parts.append(f"      <p>По времени суток сообщения ложатся неровно: "
                     f"больше всего {window}.</p>")

    districts = stats["districts"].most_common(TOP_DISTRICTS)
    if districts:
        items = "".join(
            f"<li>{escape(place)} — {value} "
            f"{plural(value, 'сообщение', 'сообщения', 'сообщений')}</li>"
            for place, value in districts)
        parts.append(
            "      <h2>Где называют чаще</h2>\n"
            "      <p>Счёт по тому, какое место назвал источник, а не по "
            "тому, где что-то произошло:</p>\n"
            f"      <ul class=\"tops\">{items}</ul>")
    return "\n".join(parts)


def page(name: str, slug: str, districts: list[str], stats: dict | None,
         neighbours: list[tuple[str, str]], updated: str) -> str:
    where = f"{preposition(name)} {inflect(name)}"
    title = f"Тревога и БПЛА {where} — карта обстановки сейчас"
    count = stats["events"] if stats else 0
    if count:
        description = (
            f"Воздушная обстановка {where}: {count} "
            f"{plural(count, 'событие', 'события', 'событий')} за 30 дней, "
            f"последнее — {moment(stats['last'])}. Тревоги, сообщения о "
            f"беспилотниках и отбои по районам, по открытым источникам.")
    else:
        description = (
            f"Воздушная обстановка {where}: за 30 дней сообщений об опасности "
            f"не было. Тревоги, сообщения о беспилотниках и отбои по районам, "
            f"по открытым источникам.")
    url = f"{SITE}/region/{slug}/"

    district_list = ""
    if districts:
        items = "".join(f"<li>{escape(item)}</li>" for item in districts)
        district_list = (
            "<h2>Районы и округа</h2>\n"
            "      <p>На карте видно обстановку по каждому из них отдельно:</p>\n"
            f"      <ul>{items}</ul>")

    neighbour_list = "".join(
        f'<li><a href="/region/{other_slug}/">{escape(other_name)}</a></li>'
        for other_name, other_slug in neighbours)

    return f"""<!doctype html>
<html lang="ru">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>{escape(title)} · Тихое небо</title>
    <meta name="description" content="{escape(description)}" />
    <link rel="canonical" href="{url}" />
    <meta property="og:type" content="website" />
    <meta property="og:site_name" content="Тихое небо" />
    <meta property="og:locale" content="ru_RU" />
    <meta property="og:url" content="{url}" />
    <meta property="og:title" content="{escape(title)}" />
    <meta property="og:description" content="{escape(description)}" />
    <meta property="og:image" content="{SITE}/preview.png" />
    <meta name="theme-color" content="#0e1211" />
    <script type="application/ld+json">
      {{"@context":"https://schema.org","@type":"BreadcrumbList","itemListElement":[
        {{"@type":"ListItem","position":1,"name":"Карта обстановки","item":"{SITE}/"}},
        {{"@type":"ListItem","position":2,"name":{json.dumps(name, ensure_ascii=False)},"item":"{url}"}}]}}
    </script>
    <style>
      body {{ margin:0; background:#0b0f0e; color:#e6ebe6;
             font:16px/1.6 Inter, system-ui, -apple-system, sans-serif; }}
      main {{ max-width:760px; margin:0 auto; padding:40px 20px 80px; }}
      h1 {{ font-size:29px; line-height:1.25; margin:0 0 14px; }}
      h2 {{ font-size:19px; margin:34px 0 10px; color:#eef2ec; }}
      p {{ color:#aab4ad; }}
      strong {{ color:#e6ebe6; }}
      nav.crumbs {{ font-size:13px; color:#7d8a83; margin:0 0 18px; }}
      nav.crumbs a {{ color:#9fd4b0; text-decoration:none; }}
      a.map {{ display:inline-block; margin:22px 0 6px; padding:13px 22px;
              background:#e93e4e; color:#fff; text-decoration:none;
              border-radius:10px; font-weight:600; }}
      ul {{ columns:2; column-gap:28px; padding-left:20px; color:#aab4ad; }}
      ul.tops, ul.around {{ columns:1; }}
      ul.around a {{ color:#9fd4b0; }}
      li {{ margin:3px 0; break-inside:avoid; }}
      footer {{ margin-top:44px; padding-top:18px; font-size:13px; color:#7d8a83;
               border-top:1px solid rgba(255,255,255,.08); }}
      footer a {{ color:#9fd4b0; }}
      @media (max-width:560px) {{ ul {{ columns:1; }} }}
    </style>
  </head>
  <body>
    <main>
      <nav class="crumbs"><a href="/">Карта обстановки</a> → {escape(name)}</nav>
      <h1>{escape(title)}</h1>
      <p>{escape(description)}</p>

      <a class="map" href="/?region={slug}">Открыть карту — {escape(name)}</a>

      {summary_block(name, stats)}

      <h2>Что показывает карта</h2>
      <p>
        Тревоги, предупреждения об опасности и отбои — так, как о них
        сообщили открытые Telegram-каналы. У каждого события видно, сколько
        независимых источников его подтвердили, и можно открыть
        первоисточник.
      </p>

      {district_list}

      <h2>Соседние регионы</h2>
      <ul class="around">{neighbour_list}</ul>

      <footer>
        Сводка обновлена {updated}. Неофициальная карта: составлена по
        публичным сообщениям, может опаздывать и ошибаться. Не принимайте по
        ней решения о личной безопасности — следуйте указаниям экстренных
        служб.
        <br /><a href="/">Вся карта обстановки по России</a>
      </footer>
    </main>
  </body>
</html>
"""


def fill_prerender(named: list, stats: dict, updated: str) -> bool:
    """Вписать в главную то, что робот без JavaScript иначе не увидит.

    SPA для поисковика — пустой div: разметка ld+json есть, а текста и
    внутренних ссылок нет. Блок между маркерами в dist/index.html живёт
    до монтирования React и виден роботу: сводка за окно и ссылки на
    все посадочные регионов. Сами маркеры лежат в index.html репозитория.
    """
    index = OUT / "index.html"
    if not index.exists():
        return False
    html = index.read_text(encoding="utf-8")
    start, end = "<!-- prerender:start -->", "<!-- prerender:end -->"
    if start not in html or end not in html:
        print("пререндер: маркеров в dist/index.html нет — блок не вписан")
        return False

    active = sum(1 for _, _, _, zone in named if stats.get(zone))
    total = sum(entry["events"] for entry in stats.values())
    lines = [
        f'<p style="margin:18px 0 6px;color:#9da8a0">За последнюю неделю — '
        f'{total} событий в {active} регионах. Обновлено {escape(updated)}.</p>',
        '<nav aria-label="Регионы"><h2 style="margin:18px 0 8px;font-size:16px">'
        'Обстановка по регионам</h2>',
        '<ul style="margin:0;padding:0;list-style:none;display:flex;'
        'flex-wrap:wrap;gap:6px 14px;max-width:900px">',
    ]
    for name, slug, _, zone in named:
        count = stats.get(zone, {}).get("events", 0)
        suffix = f" · {count}" if count else ""
        lines.append(
            f'<li><a href="/region/{slug}/" style="color:#9da8a0">'
            f'{escape(name)}{suffix}</a></li>')
    lines.append("</ul></nav>")

    head, _, rest = html.partition(start)
    _, _, tail = rest.partition(end)
    index.write_text(head + start + "\n" + "\n".join(lines) + "\n" + end + tail,
                     encoding="utf-8")
    return True


def ping_indexnow(urls: list[str], key: str) -> None:
    """Позвать роботов Яндекса и Bing сразу, не дожидаясь обхода.

    Молодой сайт ждёт первого обхода неделями. IndexNow — тот же способ
    сказать «страница изменилась», только без ручной кнопки в вебмастере.
    Google протокол не поддерживает и придёт сам.
    """
    payload = json.dumps({
        "host": SITE.removeprefix("https://"),
        "key": key,
        "keyLocation": f"{SITE}/{key}.txt",
        "urlList": urls,
    }).encode("utf-8")
    request = urllib.request.Request(
        "https://yandex.com/indexnow",
        data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    # До трёх попыток: рукопожатие с yandex.com временами не укладывается
    # в таймаут, а со второго раза проходит сразу. Повторяется только
    # сетевая ошибка — отказ робота повторять бессмысленно.
    for attempt in (1, 2, 3):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                print(f"IndexNow: {response.status}")
            return
        except urllib.error.HTTPError as error:
            # Отказ робота — не повод ронять сборку: страницы уже на месте.
            print(f"IndexNow отказал: {error.code}")
            return
        except OSError as error:
            if attempt == 3:
                print(f"IndexNow недоступен: {error}")
                return
            time.sleep(5)


def main() -> int:
    regions, by_region = load_geo()
    with closing(sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        stats = collect_stats(connection)

    today = now_utc().astimezone(MSK)
    updated = f"{today.day} {MONTHS[today.month - 1]}, {today:%H:%M} МСК"
    lastmod = today.date().isoformat()

    named = []
    for feature in regions:
        props = feature.get("properties") or {}
        # Акватории посадочной страницы не получают: она вся построена
        # вокруг районов субъекта, а у моря их нет. На карте и в ленте
        # оно при этом полноценная зона.
        if props.get("kind") == "sea":
            continue
        if props.get("name") and props.get("zone"):
            named.append((props["name"], str(props["zone"]).replace("_", "-"),
                          props["id"], props["zone"]))
    named.sort(key=lambda item: item[0])

    urls = [f"{SITE}/"]
    for index, (name, slug, source_id, zone_id) in enumerate(named):
        districts = sorted(by_region.get(source_id, []))
        # Соседи по алфавиту, кольцом: у последних регионов иначе не было бы
        # ни одной исходящей ссылки.
        neighbours = [(named[(index + step) % len(named)][0],
                       named[(index + step) % len(named)][1])
                      for step in range(1, NEIGHBOURS + 1)]
        directory = OUT / "region" / slug
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "index.html").write_text(
            page(name, slug, districts, stats.get(zone_id), neighbours, updated),
            encoding="utf-8")
        urls.append(f"{SITE}/region/{slug}/")

    entries = "\n".join(
        f"  <url><loc>{url}</loc><lastmod>{lastmod}</lastmod>"
        f"<changefreq>{'hourly' if url == SITE + '/' else 'daily'}</changefreq>"
        f"<priority>{'1.0' if url == SITE + '/' else '0.7'}</priority></url>"
        for url in urls)
    (OUT / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{entries}\n</urlset>\n", encoding="utf-8")

    with_data = sum(1 for _, _, _, zone in named if stats.get(zone))
    filled = fill_prerender(named, stats, updated)
    print(f"SEO: страниц {len(named)}, со сводкой {with_data}, "
          f"в sitemap {len(urls)} адресов"
          + (", пререндер главной обновлён" if filled else ""))

    if "--ping" in sys.argv:
        keys = [path for path in OUT.glob("*.txt") if path.stem.isalnum()
                and len(path.stem) >= 8]
        if keys:
            ping_indexnow(urls, keys[0].stem)
        else:
            print("IndexNow: ключа в dist нет, пропускаю")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
