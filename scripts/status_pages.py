"""Страницы «Какие аэропорты закрыты» и «Крымский мост сейчас».

Это два самых частых практических вопроса к воздушной обстановке: люди
проверяют, улетит ли их рейс и проедут ли они в Крым. Ответ в данных уже
есть — события «инфраструктура» несут закрытия и открытия аэропортов и
моста, — но жил он размазанным по ленте. Здесь ответ собран в один экран:
текущий статус, с какого момента, и сколько такие ограничения обычно
длятся по 30-дневной истории.

Генератор запускается из scripts.seo_pages на том же часовом таймере.
Свежесть между запусками добирает сам браузер: /aeroporty/ раз в минуту
спрашивает /api/v1/state и перекрашивает карточки живыми событиями.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import sys
from contextlib import closing
from datetime import datetime, timedelta
from html import escape
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.db import DB_PATH, ROOT
from pipeline.timeutil import MSK, now_utc

SITE = "https://tihoenebo.com"
OUT = ROOT / "dist"
WINDOW_DAYS = 30
BOT = "Tihoeneborobot"

MONTHS = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля",
          "августа", "сентября", "октября", "ноября", "декабря")

# Аэропорты, чьи ограничения встречаются в корпусе, и зоны, куда геокодер
# кладёт их события. У аэропорта, названного в честь посёлка (Туношна,
# Курумоч, Бегишево), зона и есть этот посёлок — географически это точнее
# города. Региональная зона включается только там, где в субъекте один
# гражданский аэропорт и «ограничения в аэропорту области» однозначны.
AIRPORTS = [
    ("Шереметьево", "Москва", "moskovskaya-oblast",
     ("gorodskoy_okrug_khimki_moskovskaya_oblast",)),
    ("Внуково", "Москва", "moskva", ("vnukovo_moskva",)),
    ("Домодедово", "Москва", "moskovskaya-oblast",
     ("gorodskoy_okrug_domodedovo_moskovskaya_oblast",)),
    ("Жуковский", "Москва", "moskovskaya-oblast",
     ("zhukovskiy_moskovskaya_oblast",
      "ramenskoe_ramenskiy_gorodskoy_okrug_moskovskaya_oblast")),
    ("Пулково", "Санкт-Петербург", "sankt-peterburg", ("sankt_peterburg",)),
    ("Сочи (Адлер)", "Сочи", "krasnodarskiy-kray",
     ("sochi_krasnodarskiy_kray", "sirius_sochi_krasnodarskiy_kray")),
    ("Краснодар (Пашковский)", "Краснодар", "krasnodarskiy-kray",
     ("pashkovskiy_gorodskoy_okrug_krasnodar_krasnodarskiy_kray",
      "gorodskoy_okrug_krasnodar_krasnodarskiy_kray")),
    ("Геленджик", "Геленджик", "krasnodarskiy-kray",
     ("gorodskoy_okrug_gelendzhik_krasnodarskiy_kray",)),
    ("Анапа (Витязево)", "Анапа", "krasnodarskiy-kray",
     ("anapa_krasnodarskiy_kray",)),
    ("Калуга (Грабцево)", "Калуга", "kaluzhskaya-oblast",
     ("gorodskoy_okrug_kaluga_kaluzhskaya_oblast",)),
    ("Ярославль (Туношна)", "Ярославль", "yaroslavskaya-oblast",
     ("tunoshna_yaroslavskiy_rayon_yaroslavskaya_oblast",
      "yaroslavl_yaroslavskaya_oblast")),
    ("Нижний Новгород (Стригино)", "Нижний Новгород", "nizhegorodskaya-oblast",
     ("nizhniy_novgorod_nizhegorodskaya_oblast", "nizhegorodskaya_oblast")),
    ("Самара (Курумоч)", "Самара", "samarskaya-oblast",
     ("kurumoch_volzhskiy_rayon_samarskaya_oblast",
      "samara_samarskaya_oblast", "samarskaya_oblast")),
    ("Казань", "Казань", "tatarstan",
     ("gorodskoy_okrug_kazan_tatarstan",)),
    ("Нижнекамск (Бегишево)", "Нижнекамск", "tatarstan",
     ("begishevo_zainskiy_rayon_tatarstan",
      "nizhnekamsk_nizhnekamskiy_rayon_tatarstan")),
    ("Бугульма", "Бугульма", "tatarstan",
     ("bugulma_bugulminskiy_tatarstan",)),
    ("Саратов (Гагарин)", "Саратов", "saratovskaya-oblast",
     ("saratov_saratovskaya_oblast", "saratovskaya_oblast",
      # Исторические события до починки геокодера: «аэропорт САРАТОВ
      # (Гагарин)» уезжал в город Гагарин под Смоленском.
      "gagarin_gagarinskiy_rayon_smolenskaya_oblast")),
    ("Пенза", "Пенза", "penzenskaya-oblast", ("penza_penzenskaya_oblast",)),
    ("Ульяновск (Баратаевка)", "Ульяновск", "ulyanovskaya-oblast",
     ("barataevka_gorodskoy_okrug_ulyanovsk_ulyanovskaya_oblast",)),
    ("Оренбург", "Оренбург", "orenburgskaya-oblast",
     ("gorodskoy_okrug_orenburg_orenburgskaya_oblast",
      "aeroport_orenburgskiy_rayon_orenburgskaya_oblast")),
    ("Орск", "Орск", "orenburgskaya-oblast", ("orsk_orenburgskaya_oblast",)),
    ("Уфа", "Уфа", "respublika-bashkortostan",
     ("gorodskoy_okrug_ufa_respublika_bashkortostan",)),
    ("Чебоксары", "Чебоксары", "chuvashiya",
     ("gorodskoy_okrug_cheboksary_chuvashiya",)),
    ("Киров (Победилово)", "Киров", "kirovskaya-oblast",
     ("gorodskoy_okrug_kirov_kirovskaya_oblast",)),
    ("Ижевск", "Ижевск", "udmurtiya",
     ("gorodskoy_okrug_izhevsk_udmurtiya",)),
    ("Пермь (Большое Савино)", "Пермь", "permskiy-kray",
     ("perm_permskiy_okrug_permskiy_kray",
      "bolshoe_savino_permskiy_rayon_permskiy_kray")),
    ("Екатеринбург (Кольцово)", "Екатеринбург", "sverdlovskaya-oblast",
     ("gorodskoy_okrug_ekaterinburg_sverdlovskaya_oblast",
      # Исторические события: «Кольцово» уезжало в наукоград под
      # Новосибирском, пока геокодер не выучил этот омоним.
      "koltsovo_rabochiy_poselok_koltsovo_novosibirskaya_oblast")),
    ("Челябинск (Баландино)", "Челябинск", "chelyabinskaya-oblast",
     ("balandino_chesmenskiy_rayon_chelyabinskaya_oblast",
      "chelyabinsk_chelyabinskiy_okrug_chelyabinskaya_oblast")),
    ("Волгоград", "Волгоград", "volgogradskaya-oblast",
     ("volgograd_volgogradskaya_oblast",
      "aeroport_volgograd_volgogradskaya_oblast")),
    ("Тамбов (Донское)", "Тамбов", "tambovskaya-oblast",
     ("tambov_tambovskaya_oblast",
      "donskoe_tambovskiy_rayon_tambovskaya_oblast")),
    ("Воронеж", "Воронеж", "voronezhskaya-oblast",
     ("voronezh_voronezhskaya_oblast",)),
    ("Белгород", "Белгород", "belgorodskaya-oblast",
     ("gorodskoy_okrug_belgorod_belgorodskaya_oblast",)),
    ("Псков", "Псков", "pskovskaya-oblast", ("pskov_pskovskaya_oblast",)),
    ("Иваново", "Иваново", "ivanovskaya-oblast",
     ("ivanovo_ivanovskaya_oblast",)),
    ("Череповец", "Череповец", "vologodskaya-oblast",
     ("cherepovets_vologodskaya_oblast",)),
    ("Астрахань", "Астрахань", "astrakhanskaya-oblast",
     ("gorodskoy_okrug_astrakhan_astrakhanskaya_oblast",)),
    ("Махачкала", "Махачкала", "dagestan",
     ("gorodskoy_okrug_makhachkala_dagestan",)),
]

KERCH_ZONE = "kerch_leninskiy_rayon_respublika_krym"
BRIDGE_RE = re.compile(r"крымск\w+\s+мост|керченск\w+\s+мост|мост\w*\s+"
                       r"[^.!\n]{0,20}?крымск", re.IGNORECASE)
BRIDGE_CLOSE_RE = re.compile(
    r"перекрыт|закрыт|приостановлен|остановлен", re.IGNORECASE)
BRIDGE_OPEN_RE = re.compile(
    r"возобновлен|восстановлен|открыт", re.IGNORECASE)


def zone_start_payload(zone_id: str) -> str:
    """Payload диплинка бота — та же формула, что в api/telegram.py.

    Дублируется намеренно: генератор ходит без fastapi-окружения. Тест
    test_status_pages сверяет обе реализации.
    """
    if len(zone_id) <= 62:
        return "w_" + zone_id
    return "wh" + hashlib.md5(zone_id.encode("utf-8")).hexdigest()[:12]


def bot_cta(zone_id: str | None, place: str | None) -> str:
    """Кнопка подписки на уведомления бота — воронка из посадочных страниц."""
    link = f"https://t.me/{BOT}"
    if zone_id:
        link += f"?start={zone_start_payload(zone_id)}"
    label = (f"Получать уведомления — {place}" if place
             else "Получать уведомления в Telegram")
    return (
        '<div class="cta">'
        '<p><strong>Тревоги, БПЛА и отбои — сообщением в Telegram.</strong> '
        'Бот напишет, когда обстановка изменится'
        + (f" в месте «{escape(place)}»" if place else "") + ".</p>"
        f'<a class="bot" href="{escape(link)}">{escape(label)}</a>'
        "</div>")


def moment(iso: str) -> str:
    stamp = datetime.fromisoformat(iso).astimezone(MSK)
    return f"{stamp.day} {MONTHS[stamp.month - 1]} {stamp:%H:%M} МСК"


def minutes_word(minutes: int) -> str:
    if minutes >= 120:
        hours = round(minutes / 60)
        return f"около {hours} часов" if hours >= 5 else f"около {hours} ч"
    return f"{minutes} мин"


def plural(count: int, one: str, few: str, many: str) -> str:
    tail, last_two = count % 10, count % 100
    if tail == 1 and last_two != 11:
        return one
    if 2 <= tail <= 4 and not 12 <= last_two <= 14:
        return few
    return many


def head(title: str, description: str, url: str, extra_ld: list[str]) -> str:
    ld = "\n    ".join(
        f'<script type="application/ld+json">{block}</script>'
        for block in extra_ld)
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
    {ld}
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
      .status {{ display:flex; flex-wrap:wrap; gap:10px; margin:20px 0;
                padding:0; list-style:none; }}
      .status li {{ flex:1 1 230px; border:1px solid rgba(255,255,255,.09);
                   border-radius:12px; padding:12px 14px; }}
      .status .state {{ font-weight:600; }}
      .closed .state {{ color:#ff7b72; }}
      .open .state {{ color:#7ddba3; }}
      .quiet .state {{ color:#8b9a91; }}
      .status .apt {{ font-weight:600; color:#eef2ec; }}
      .status .since, .status .hist {{ font-size:13px; color:#8b9a91; }}
      .status a {{ color:#9fd4b0; text-decoration:none; font-size:13px; }}
      table {{ border-collapse:collapse; width:100%; font-size:14px; }}
      th, td {{ text-align:left; padding:7px 10px;
               border-bottom:1px solid rgba(255,255,255,.07); }}
      th {{ color:#8b9a91; font-weight:500; }}
      td {{ color:#c6cfc8; }}
      .cta {{ border:1px solid rgba(159,212,176,.35); border-radius:12px;
             padding:16px 18px; margin:30px 0; }}
      .cta p {{ margin:0 0 12px; }}
      a.bot {{ display:inline-block; padding:11px 20px; background:#2aabee;
              color:#fff; text-decoration:none; border-radius:10px;
              font-weight:600; }}
      footer {{ margin-top:44px; padding-top:18px; font-size:13px;
               color:#7d8a83; border-top:1px solid rgba(255,255,255,.08); }}
      footer a {{ color:#9fd4b0; }}
      @media (max-width:560px) {{ .status li {{ flex-basis:100%; }} }}
    </style>
  </head>
  <body>
    <main>
"""


FOOTER = """
      <footer>
        Неофициальная сводка: составлена по публичным сообщениям
        Telegram-каналов и Росавиации в их пересказе, может опаздывать и
        ошибаться. Не планируйте по ней поездки и рейсы — проверяйте
        официальные источники: аэропорты, авиакомпании, оперативные службы.
        <br /><a href="/">Карта обстановки</a> ·
        <a href="/aeroporty/">Аэропорты</a> ·
        <a href="/krymskiy-most/">Крымский мост</a> ·
        <a href="/marshruty/">Маршруты БПЛА</a>
      </footer>
    </main>
  </body>
</html>
"""


def breadcrumb_ld(name: str, url: str) -> str:
    return json.dumps({
        "@context": "https://schema.org", "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Карта обстановки",
             "item": f"{SITE}/"},
            {"@type": "ListItem", "position": 2, "name": name, "item": url},
        ],
    }, ensure_ascii=False)


def faq_ld(pairs: list[tuple[str, str]]) -> str:
    return json.dumps({
        "@context": "https://schema.org", "@type": "FAQPage",
        "mainEntity": [
            {"@type": "Question", "name": question,
             "acceptedAnswer": {"@type": "Answer", "text": answer}}
            for question, answer in pairs
        ],
    }, ensure_ascii=False)


def faq_html(pairs: list[tuple[str, str]]) -> str:
    blocks = "".join(
        f"<h3>{escape(question)}</h3>\n<p>{escape(answer)}</p>\n"
        for question, answer in pairs)
    return f"<h2>Вопросы и ответы</h2>\n{blocks}"


# --- Аэропорты ---------------------------------------------------------------

def airport_rows(connection: sqlite3.Connection) -> list[dict]:
    """Статус и месячная история каждого аэропорта из реестра."""
    since = (now_utc() - timedelta(days=WINDOW_DAYS)).isoformat()
    rows = []
    for name, city, region_slug, zone_ids in AIRPORTS:
        marks = ",".join("?" for _ in zone_ids)
        latest = connection.execute(
            f"""SELECT first_seen_at, resolved_at, status FROM events
                WHERE threat_type = 'airport' AND zone_id IN ({marks})
                ORDER BY first_seen_at DESC LIMIT 1""", zone_ids).fetchone()
        # Одна новость «аэропорт закрыт» рождает событие в каждой зоне
        # аэропорта (Туношна и Ярославль, Сочи и Сириус) — считать их
        # порознь значило бы удвоить историю. Склеиваем по минуте начала.
        events = connection.execute(
            f"""SELECT first_seen_at, resolved_at FROM events
                WHERE threat_type = 'airport' AND zone_id IN ({marks})
                  AND first_seen_at >= ?
                ORDER BY first_seen_at""", (*zone_ids, since)).fetchall()
        by_minute: dict[str, float | None] = {}
        for event in events:
            minute = event["first_seen_at"][:16]
            duration = None
            if event["resolved_at"]:
                duration = (datetime.fromisoformat(event["resolved_at"])
                            - datetime.fromisoformat(event["first_seen_at"])
                            ).total_seconds() / 60
            if minute not in by_minute or (
                    duration or 0) > (by_minute[minute] or 0):
                by_minute[minute] = duration
        durations = [d for d in by_minute.values() if d and d >= 1]
        closed = bool(latest) and latest["status"] in ("active", "fading")
        rows.append({
            "name": name, "city": city, "region_slug": region_slug,
            "zone_ids": zone_ids, "closed": closed,
            "since": latest["first_seen_at"] if closed else None,
            "reopened": (latest["resolved_at"]
                         if latest and not closed else None),
            "closures": len(by_minute),
            "avg_minutes": (int(round(sum(durations) / len(durations)))
                            if durations else None),
        })
    rows.sort(key=lambda r: (not r["closed"], -r["closures"], r["name"]))
    return rows


def airport_card(row: dict) -> str:
    classes = ("closed" if row["closed"]
               else "open" if row["closures"] else "quiet")
    if row["closed"]:
        state = "закрыт — действуют ограничения"
        since = (f'<div class="since">ограничения с {moment(row["since"])}'
                 "</div>" if row["since"] else "")
    elif row["reopened"]:
        state = "работает"
        since = (f'<div class="since">ограничения сняты '
                 f'{moment(row["reopened"])}</div>')
    elif row["closures"]:
        state = "работает"
        since = ""
    else:
        state = "ограничений не сообщалось"
        since = ""
    if row["closures"]:
        avg = (f", в среднем {minutes_word(row['avg_minutes'])}"
               if row["avg_minutes"] else "")
        hist = (f'<div class="hist">за 30 дней: {row["closures"]} '
                f'{plural(row["closures"], "закрытие", "закрытия", "закрытий")}'
                f"{avg}</div>")
    else:
        hist = '<div class="hist">за 30 дней закрытий не было</div>'
    zones = escape(json.dumps(row["zone_ids"]), quote=True)
    return (f'<li class="{classes}" data-zones=\'{zones}\'>'
            f'<div class="apt">{escape(row["name"])}</div>'
            f'<div class="state">{state}</div>{since}{hist}'
            f'<a href="/region/{row["region_slug"]}/">Обстановка — '
            f'{escape(row["city"])}</a></li>')


LIVE_JS = """
<script>
(function () {
  function refresh() {
    fetch("/api/v1/state").then(function (r) { return r.json(); })
      .then(function (state) {
        var closedZones = {};
        (state.events || []).forEach(function (event) {
          if (event.threat_type !== "airport") return;
          if (event.status === "resolved") return;
          (event.zone_path || [event.zone_id]).concat([event.zone_id])
            .forEach(function (zone) { closedZones[zone] = event.first_seen_at; });
        });
        document.querySelectorAll("#airports li[data-zones]").forEach(function (card) {
          var zones = JSON.parse(card.dataset.zones);
          var since = null;
          zones.forEach(function (zone) {
            if (closedZones[zone]) since = closedZones[zone];
          });
          var wasClosed = card.classList.contains("closed");
          if (!!since === wasClosed) return;
          card.classList.toggle("closed", !!since);
          card.classList.toggle("open", !since);
          var state = card.querySelector(".state");
          if (state) state.textContent = since
            ? "закрыт — действуют ограничения" : "работает";
        });
      }).catch(function () {});
  }
  refresh();
  setInterval(refresh, 60000);
})();
</script>
"""


def airports_page(rows: list[dict], updated: str) -> str:
    closed = [row for row in rows if row["closed"]]
    url = f"{SITE}/aeroporty/"
    title = "Какие аэропорты закрыты сейчас — карта ограничений онлайн"
    if closed:
        names = ", ".join(row["name"] for row in closed[:4])
        description = (
            f"Ограничения на приём и выпуск сейчас: {names}"
            + ("и другие" if len(closed) > 4 else "")
            + f". Статус {len(rows)} аэропортов по сообщениям о воздушной "
              f"обстановке, история закрытий за 30 дней.")
    else:
        description = (
            f"Сейчас сообщений о закрытых аэропортах нет. Статус {len(rows)} "
            f"аэропортов по сообщениям о воздушной обстановке онлайн, "
            f"история ограничений за 30 дней.")
    total = sum(row["closures"] for row in rows)
    avgs = [row["avg_minutes"] for row in rows if row["avg_minutes"]]
    typical = int(sum(avgs) / len(avgs)) if avgs else None
    faq = [
        ("Почему закрывают аэропорты",
         "При угрозе БПЛА Росавиация вводит план «Ковёр» — временные "
         "ограничения на приём и выпуск воздушных судов. Рейсы уходят на "
         "запасные аэродромы или ждут на земле, пока угроза не снята."),
        ("Как долго длятся ограничения",
         (f"По нашей статистике за 30 дней — {plural(total, 'одно закрытие', str(total) + ' закрытия', str(total) + ' закрытий')} "
          f"со средней длительностью {minutes_word(typical)}. Бывают и "
          f"многочасовые: всё зависит от обстановки."
          if typical else
          "За последние 30 дней закрытий в отслеживаемых аэропортах не "
          "было.")),
        ("Откуда данные и насколько они точны",
         "Карта собирает открытые сообщения Telegram-каналов, включая "
         "пересказы сводок Росавиации. Это неофициальная информация: она "
         "может опаздывать и ошибаться. Статус рейса проверяйте на сайте "
         "аэропорта или у авиакомпании."),
        ("Аэропорт открыли, а рейса всё нет — почему",
         "После снятия ограничений расписание восстанавливается ещё "
         "несколько часов: борта возвращаются с запасных аэродромов, "
         "экипажи выходят за пределы рабочего времени. Задержки после "
         "открытия — обычное дело."),
    ]
    cards = "".join(airport_card(row) for row in rows)
    ld = [
        breadcrumb_ld("Аэропорты", url),
        faq_ld(faq),
        json.dumps({
            "@context": "https://schema.org", "@type": "WebPage",
            "url": url, "name": title,
            "isPartOf": {"@type": "WebSite", "name": "Тихое небо",
                         "url": f"{SITE}/"},
        }, ensure_ascii=False),
    ]
    if closed:
        summary = (f"Сейчас ограничения действуют в "
                   f"{len(closed)} {plural(len(closed), 'аэропорту', 'аэропортах', 'аэропортах')}: "
                   + ", ".join(f"<strong>{escape(row['name'])}</strong>"
                               for row in closed) + ".")
    else:
        summary = ("Сейчас сообщений о действующих ограничениях нет — все "
                   "отслеживаемые аэропорты работают.")
    return (
        head(title, description, url, ld)
        + f"""      <nav class="crumbs"><a href="/">Карта обстановки</a> → Аэропорты</nav>
      <h1>Какие аэропорты сейчас закрыты</h1>
      <p>{summary} Статус обновляется по живым сообщениям о воздушной
      обстановке; красным — аэропорты, где прямо сейчас действуют
      ограничения на приём и выпуск.</p>
      <ul class="status" id="airports">{cards}</ul>
      <a class="map" href="/">Открыть карту обстановки</a>
      {bot_cta(None, None)}
      {faq_html(faq)}
      <p>Сводка обновлена {updated}; карточки дообновляются в браузере
      раз в минуту.</p>{FOOTER}{LIVE_JS}""")


# --- Крымский мост -----------------------------------------------------------

def bridge_timeline(connection: sqlite3.Connection) -> list[dict]:
    """Перекрытия и открытия моста из самих сообщений.

    События здесь не годятся посредником: до починки разбора «движение
    возобновлено» рождало новое событие вместо закрытия старого, и пары
    «перекрыт — открыт» в событиях исторически нет. Сообщения надёжнее:
    каждое явно говорит, перекрыли или возобновили.
    """
    since = (now_utc() - timedelta(days=WINDOW_DAYS)).isoformat()
    rows = connection.execute(
        """SELECT posted_at, text FROM raw_messages
           WHERE posted_at >= ? AND text LIKE '%мост%'
           ORDER BY posted_at""", (since,)).fetchall()
    steps = []
    for row in rows:
        text = row["text"] or ""
        if not BRIDGE_RE.search(text):
            continue
        first = text.split("\n", 1)[0] + " " + text[:200]
        opened = bool(BRIDGE_OPEN_RE.search(first))
        closed = bool(BRIDGE_CLOSE_RE.search(first))
        if opened == closed:
            continue
        state = "open" if opened else "closed"
        if steps and steps[-1]["state"] == state:
            continue
        steps.append({"at": row["posted_at"], "state": state})
    return steps


def bridge_page(connection: sqlite3.Connection, updated: str) -> str:
    steps = bridge_timeline(connection)
    url = f"{SITE}/krymskiy-most/"
    closed_now = bool(steps) and steps[-1]["state"] == "closed"
    since = steps[-1]["at"] if steps else None

    closures = []
    open_at = None
    for step in steps:
        if step["state"] == "closed":
            open_at = step["at"]
        elif open_at:
            minutes = int((datetime.fromisoformat(step["at"])
                           - datetime.fromisoformat(open_at)).total_seconds()
                          // 60)
            # Пары короче трёх минут — пересказ одной новости двумя
            # каналами, а не настоящее перекрытие.
            if minutes >= 3:
                closures.append({"from": open_at, "to": step["at"],
                                 "minutes": minutes})
            open_at = None
    closures.reverse()
    avg = (int(sum(c["minutes"] for c in closures) / len(closures))
           if closures else None)

    title = "Крымский мост сейчас — открыт или закрыт, обстановка онлайн"
    state_word = ("перекрыт" if closed_now else "открыт")
    if closed_now and since:
        description = (f"Движение по Крымскому мосту перекрыто с "
                       f"{moment(since)}. Сколько обычно длится перекрытие "
                       f"и история за 30 дней — по живым сообщениям.")
    else:
        tail = (f"За 30 дней — {len(closures)} "
                + plural(len(closures), "перекрытие", "перекрытия",
                         "перекрытий")
                + (f", в среднем {minutes_word(avg)}" if avg else "")
                + "." if closures else "")
        description = (f"Движение по Крымскому мосту сейчас открыто. {tail} "
                       f"Статус и история перекрытий онлайн по живым "
                       f"сообщениям.")

    history_rows = "".join(
        f"<tr><td>{moment(c['from'])}</td><td>{moment(c['to'])}</td>"
        f"<td>{minutes_word(c['minutes'])}</td></tr>"
        for c in closures[:20])
    history = (
        "<h2>Перекрытия за 30 дней</h2>\n"
        "<table><tr><th>Перекрыли</th><th>Открыли</th><th>Длилось</th></tr>"
        f"{history_rows}</table>" if closures else
        "<h2>Перекрытия за 30 дней</h2>\n<p>За последний месяц сообщений о "
        "перекрытии моста не было.</p>")

    faq = [
        ("Почему перекрывают Крымский мост",
         "Движение останавливают при угрозе БПЛА или безэкипажных катеров в "
         "Керченском проливе и на подходах. Это профилактическая мера: "
         "перекрытие само по себе не означает атаку на мост."),
        ("Сколько обычно длится перекрытие",
         (f"За последние 30 дней — {len(closures)} "
          f"{plural(len(closures), 'перекрытие', 'перекрытия', 'перекрытий')}, "
          f"в среднем {minutes_word(avg)}. Отдельные перекрытия длятся "
          f"дольше — зависит от обстановки."
          if avg else
          "За последние 30 дней перекрытий не фиксировалось.")),
        ("Что делать, если вы на мосту или в очереди",
         "Следуйте указаниям сотрудников на месте. При перекрытии "
         "рекомендуют оставаться в машине или пройти в укрытие на "
         "досмотровых пунктах — как скажут на месте."),
        ("Откуда данные",
         "Открытые сообщения оперативных Telegram-каналов Крыма и Кубани. "
         "Это неофициальная сводка: проверяйте состояние моста по "
         "официальным источникам перед поездкой."),
    ]
    ld = [
        breadcrumb_ld("Крымский мост", url),
        faq_ld(faq),
        json.dumps({
            "@context": "https://schema.org", "@type": "WebPage",
            "url": url, "name": title,
            "about": {"@type": "Place", "name": "Крымский мост"},
            "isPartOf": {"@type": "WebSite", "name": "Тихое небо",
                         "url": f"{SITE}/"},
        }, ensure_ascii=False),
    ]
    if closed_now:
        status_line = (
            f'<p class="closed"><strong>Движение перекрыто</strong>'
            + (f" — с {moment(since)}" if since else "") + ".</p>")
    else:
        last_open = steps[-1]["at"] if steps else None
        status_line = (
            "<p><strong>Движение открыто</strong>"
            + (f" — возобновлено {moment(last_open)}"
               if last_open else "") + ".</p>")
    return (
        head(title, description, url, ld)
        + f"""      <nav class="crumbs"><a href="/">Карта обстановки</a> → Крымский мост</nav>
      <h1>Крымский мост сейчас {state_word}</h1>
      {status_line}
      <p>Статус собран по живым сообщениям оперативных каналов о
      перекрытии и возобновлении движения. Свежие сообщения по Керчи и
      проливу — на карте.</p>
      <a class="map" href="/?region=respublika-krym">Открыть карту — Крым</a>
      {bot_cta(KERCH_ZONE, "Керчь")}
      {history}
      {faq_html(faq)}
      <p>Сводка обновлена {updated}.</p>{FOOTER}""")


def main() -> list[str]:
    """Собрать обе страницы; вернуть их адреса для sitemap."""
    today = now_utc().astimezone(MSK)
    updated = f"{today.day} {MONTHS[today.month - 1]}, {today:%H:%M} МСК"
    with closing(sqlite3.connect(f"file:{DB_PATH}?mode=ro",
                                 uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        rows = airport_rows(connection)
        airports_html = airports_page(rows, updated)
        bridge_html = bridge_page(connection, updated)
    for path, html in (("aeroporty", airports_html),
                       ("krymskiy-most", bridge_html)):
        directory = OUT / path
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "index.html").write_text(html, encoding="utf-8")
    closed = sum(1 for row in rows if row["closed"])
    print(f"Статусные страницы: аэропортов {len(rows)} "
          f"(закрыто {closed}), мост собран")
    return [f"{SITE}/aeroporty/", f"{SITE}/krymskiy-most/"]


if __name__ == "__main__":
    main()
