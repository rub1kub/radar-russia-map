"""Страницы «Какие аэропорты закрыты» и «Крымский мост сейчас».

Это два самых частых практических вопроса к воздушной обстановке: люди
проверяют, улетит ли их рейс и проедут ли они в Крым. Ответ собран в
один экран: статус в первой строке, с какого момента, и сколько такое
обычно длится по 30-дневной истории.

Статусы аэропортов считаются по «Говорит Росавиация» (favt_info) — это
единственный официальный первоисточник ограничений, он уже в сборе.
Сообщение само называет аэропорты («Аэропорт ЯРОСЛАВЛЬ (Туношна)»,
«Аэропорты — ВНУКОВО — ШЕРЕМЕТЬЕВО») и глагол «ВВЕДЕНЫ»/«СНЯТЫ», поэтому
здесь не нужен геокодер: пары «закрыт — открыт» складываются из самих
слов регулятора. Мост считается так же — по словам оперативных каналов
«перекрыто»/«возобновлено».

Генератор запускается из scripts.seo_pages на часовом таймере. Свежесть
между запусками добирает браузер: страница аэропортов раз в минуту
спрашивает /api/v1/state и перекрашивает карточки живыми событиями.

Дизайн-макеты: артефакт «Статусные страницы Тихого неба». Палитра и
радиусы сняты с живой карты (src/styles.css).
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
import sqlite3
import sys
from contextlib import closing
from datetime import datetime, timedelta
from html import escape
from pathlib import Path
from statistics import median

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.db import DB_PATH, ROOT
from pipeline.timeutil import MSK, now_utc

SITE = "https://tihoenebo.com"
OUT = ROOT / "dist"
WINDOW_DAYS = 30
BOT = "Tihoeneborobot"
FAVT_SOURCES = ("favt_info", "ch1938794947")
# Закрытие без снятия старше суток — скорее пропущенное сообщение, чем
# сутки без полётов: такой аэропорт не показывается закрытым.
STALE_CLOSE_HOURS = 24

MONTHS = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля",
          "августа", "сентября", "октября", "ноября", "декабря")

# Аэропорты, как их называет Росавиация (ЗАГЛАВНЫМИ), с человеческим
# именем, городом, ссылкой на региональную сводку и зонами карты — по
# зонам живая страница перекрашивает карточки между часовыми пересборками.
# Реестр не обязан быть полным: аэропорт, впервые названный регулятором,
# добавится на страницу сам, без ссылки и зон.
AIRPORTS = [
    ("ШЕРЕМЕТЬЕВО", "Шереметьево", "Москва", "moskovskaya-oblast",
     ("gorodskoy_okrug_khimki_moskovskaya_oblast",)),
    ("ВНУКОВО", "Внуково", "Москва", "moskva", ("vnukovo_moskva",)),
    ("ДОМОДЕДОВО", "Домодедово", "Москва", "moskovskaya-oblast",
     ("gorodskoy_okrug_domodedovo_moskovskaya_oblast",)),
    ("ЖУКОВСКИЙ", "Жуковский", "Москва", "moskovskaya-oblast",
     ("zhukovskiy_moskovskaya_oblast",
      "ramenskoe_ramenskiy_gorodskoy_okrug_moskovskaya_oblast")),
    ("ПУЛКОВО", "Пулково", "Санкт-Петербург", "sankt-peterburg",
     ("sankt_peterburg",)),
    ("СОЧИ", "Сочи (Адлер)", "Сочи", "krasnodarskiy-kray",
     ("sochi_krasnodarskiy_kray", "sirius_sochi_krasnodarskiy_kray")),
    ("КРАСНОДАР", "Краснодар (Пашковский)", "Краснодар", "krasnodarskiy-kray",
     ("pashkovskiy_gorodskoy_okrug_krasnodar_krasnodarskiy_kray",
      "gorodskoy_okrug_krasnodar_krasnodarskiy_kray")),
    ("ГЕЛЕНДЖИК", "Геленджик", "Геленджик", "krasnodarskiy-kray",
     ("gorodskoy_okrug_gelendzhik_krasnodarskiy_kray",)),
    ("АНАПА", "Анапа (Витязево)", "Анапа", "krasnodarskiy-kray",
     ("anapa_krasnodarskiy_kray",)),
    ("КАЛУГА", "Калуга (Грабцево)", "Калуга", "kaluzhskaya-oblast",
     ("gorodskoy_okrug_kaluga_kaluzhskaya_oblast",)),
    ("ЯРОСЛАВЛЬ", "Ярославль (Туношна)", "Ярославль", "yaroslavskaya-oblast",
     ("tunoshna_yaroslavskiy_rayon_yaroslavskaya_oblast",
      "yaroslavl_yaroslavskaya_oblast")),
    ("НИЖНИЙ НОВГОРОД", "Нижний Новгород (Стригино)", "Нижний Новгород",
     "nizhegorodskaya-oblast",
     ("nizhniy_novgorod_nizhegorodskaya_oblast", "nizhegorodskaya_oblast")),
    ("САМАРА", "Самара (Курумоч)", "Самара", "samarskaya-oblast",
     ("kurumoch_volzhskiy_rayon_samarskaya_oblast",
      "samara_samarskaya_oblast", "samarskaya_oblast")),
    ("КАЗАНЬ", "Казань", "Казань", "tatarstan",
     ("gorodskoy_okrug_kazan_tatarstan",)),
    ("НИЖНЕКАМСК", "Нижнекамск (Бегишево)", "Нижнекамск", "tatarstan",
     ("begishevo_zainskiy_rayon_tatarstan",
      "nizhnekamsk_nizhnekamskiy_rayon_tatarstan")),
    ("БУГУЛЬМА", "Бугульма", "Бугульма", "tatarstan",
     ("bugulma_bugulminskiy_tatarstan",)),
    ("САРАТОВ", "Саратов (Гагарин)", "Саратов", "saratovskaya-oblast",
     ("saratov_saratovskaya_oblast", "saratovskaya_oblast")),
    ("ПЕНЗА", "Пенза", "Пенза", "penzenskaya-oblast",
     ("penza_penzenskaya_oblast",)),
    ("УЛЬЯНОВСК", "Ульяновск (Баратаевка)", "Ульяновск", "ulyanovskaya-oblast",
     ("barataevka_gorodskoy_okrug_ulyanovsk_ulyanovskaya_oblast",)),
    ("ОРЕНБУРГ", "Оренбург", "Оренбург", "orenburgskaya-oblast",
     ("gorodskoy_okrug_orenburg_orenburgskaya_oblast",)),
    ("ОРСК", "Орск", "Орск", "orenburgskaya-oblast",
     ("orsk_orenburgskaya_oblast",)),
    ("УФА", "Уфа", "Уфа", "respublika-bashkortostan",
     ("gorodskoy_okrug_ufa_respublika_bashkortostan",)),
    ("ЧЕБОКСАРЫ", "Чебоксары", "Чебоксары", "chuvashiya",
     ("gorodskoy_okrug_cheboksary_chuvashiya",)),
    ("КИРОВ", "Киров (Победилово)", "Киров", "kirovskaya-oblast",
     ("gorodskoy_okrug_kirov_kirovskaya_oblast",)),
    ("ИЖЕВСК", "Ижевск", "Ижевск", "udmurtiya",
     ("gorodskoy_okrug_izhevsk_udmurtiya",)),
    ("САРАНСК", "Саранск", "Саранск", "mordoviya",
     ("gorodskoy_okrug_saransk_mordoviya",)),
    ("ПЕРМЬ", "Пермь (Большое Савино)", "Пермь", "permskiy-kray",
     ("perm_permskiy_okrug_permskiy_kray",
      "bolshoe_savino_permskiy_rayon_permskiy_kray")),
    ("ЕКАТЕРИНБУРГ", "Екатеринбург (Кольцово)", "Екатеринбург",
     "sverdlovskaya-oblast",
     ("gorodskoy_okrug_ekaterinburg_sverdlovskaya_oblast",)),
    ("ЧЕЛЯБИНСК", "Челябинск (Баландино)", "Челябинск", "chelyabinskaya-oblast",
     ("balandino_chesmenskiy_rayon_chelyabinskaya_oblast",
      "chelyabinsk_chelyabinskiy_okrug_chelyabinskaya_oblast")),
    ("ТЮМЕНЬ", "Тюмень (Рощино)", "Тюмень", "tyumenskaya-oblast",
     ("gorodskoy_okrug_tyumen_tyumenskaya_oblast",)),
    ("ОМСК", "Омск", "Омск", "omskaya-oblast",
     ("gorodskoy_okrug_omsk_omskaya_oblast",)),
    ("ВОЛГОГРАД", "Волгоград", "Волгоград", "volgogradskaya-oblast",
     ("volgograd_volgogradskaya_oblast",)),
    ("ТАМБОВ", "Тамбов (Донское)", "Тамбов", "tambovskaya-oblast",
     ("tambov_tambovskaya_oblast",
      "donskoe_tambovskiy_rayon_tambovskaya_oblast")),
    ("ВОРОНЕЖ", "Воронеж", "Воронеж", "voronezhskaya-oblast",
     ("voronezh_voronezhskaya_oblast",)),
    ("БЕЛГОРОД", "Белгород", "Белгород", "belgorodskaya-oblast",
     ("gorodskoy_okrug_belgorod_belgorodskaya_oblast",)),
    ("ПСКОВ", "Псков", "Псков", "pskovskaya-oblast",
     ("pskov_pskovskaya_oblast",)),
    ("ИВАНОВО", "Иваново", "Иваново", "ivanovskaya-oblast",
     ("ivanovo_ivanovskaya_oblast",)),
    ("ЧЕРЕПОВЕЦ", "Череповец", "Череповец", "vologodskaya-oblast",
     ("cherepovets_vologodskaya_oblast",)),
    ("СТАВРОПОЛЬ", "Ставрополь (Шпаковское)", "Ставрополь",
     "stavropolskiy-kray", ("stavropol_stavropolskiy_kray",)),
    ("АСТРАХАНЬ", "Астрахань", "Астрахань", "astrakhanskaya-oblast",
     ("gorodskoy_okrug_astrakhan_astrakhanskaya_oblast",)),
    ("МАХАЧКАЛА", "Махачкала", "Махачкала", "dagestan",
     ("gorodskoy_okrug_makhachkala_dagestan",)),
]
KNOWN_KEYS = [key for key, *_ in AIRPORTS]

KERCH_ZONE = "kerch_leninskiy_rayon_respublika_krym"
BRIDGE_RE = re.compile(r"крымск\w+\s+мост|керченск\w+\s+мост|мост\w*\s+"
                       r"[^.!\n]{0,20}?крымск", re.IGNORECASE)
BRIDGE_CLOSE_RE = re.compile(
    r"перекрыт|закрыт|приостановлен|остановлен", re.IGNORECASE)
BRIDGE_OPEN_RE = re.compile(
    r"возобновлен|восстановлен|открыт", re.IGNORECASE)

AIRPORT_NAME_RE = re.compile(r"[А-ЯЁ][А-ЯЁ\- ]{1,30}")


# --- Общие мелочи ------------------------------------------------------------

def zone_start_payload(zone_id: str) -> str:
    """Payload диплинка бота — та же формула, что в api/telegram.py.

    Дублируется намеренно: генератор ходит без fastapi-окружения. Тест
    test_status_pages сверяет обе реализации.
    """
    if len(zone_id) <= 62:
        return "w_" + zone_id
    return "wh" + hashlib.md5(zone_id.encode("utf-8")).hexdigest()[:12]


def moment(iso: str) -> str:
    stamp = datetime.fromisoformat(iso).astimezone(MSK)
    return f"{stamp.day} {MONTHS[stamp.month - 1]} в {stamp:%H:%M}"


def clock(iso: str) -> str:
    stamp = datetime.fromisoformat(iso).astimezone(MSK)
    today = now_utc().astimezone(MSK).date()
    if stamp.date() == today:
        return f"сегодня в {stamp:%H:%M}"
    if (today - stamp.date()).days == 1:
        return f"вчера в {stamp:%H:%M}"
    return f"{stamp.day} {MONTHS[stamp.month - 1]} в {stamp:%H:%M}"


def since_clock(iso: str) -> str:
    """«с 08:46», «со вчерашних 22:59», «с 14 августа» — после «Закрыт …»."""
    stamp = datetime.fromisoformat(iso).astimezone(MSK)
    today = now_utc().astimezone(MSK).date()
    if stamp.date() == today:
        return f"с {stamp:%H:%M}"
    if (today - stamp.date()).days == 1:
        return f"со вчерашних {stamp:%H:%M}"
    return f"с {stamp.day} {MONTHS[stamp.month - 1]}"


def span_text(start_iso: str, end_iso: str) -> str:
    """«18 августа, 04:12 → 05:22» — дата второй раз не повторяется."""
    start = datetime.fromisoformat(start_iso).astimezone(MSK)
    end = datetime.fromisoformat(end_iso).astimezone(MSK)
    left = f"{start.day} {MONTHS[start.month - 1]}, {start:%H:%M}"
    if start.date() == end.date():
        return f"{left} → {end:%H:%M}"
    return f"{left} → {end.day} {MONTHS[end.month - 1]}, {end:%H:%M}"


def minutes_word(minutes: int) -> str:
    if minutes >= 100:
        hours = minutes / 60
        rounded = round(hours * 2) / 2
        text = (str(int(rounded)) if rounded == int(rounded)
                else f"{rounded:.1f}".replace(".", ","))
        return f"{text} ч"
    return f"{int(minutes)} мин"


def plural(count: int, one: str, few: str, many: str) -> str:
    tail, last_two = count % 10, count % 100
    if tail == 1 and last_two != 11:
        return one
    if 2 <= tail <= 4 and not 12 <= last_two <= 14:
        return few
    return many


def bot_cta(zone_id: str | None, lead: str, tail: str) -> str:
    link = f"https://t.me/{BOT}"
    if zone_id:
        link += f"?start={zone_start_payload(zone_id)}"
    return (
        '<div class="cta"><p><strong>' + escape(lead) + "</strong> "
        + escape(tail) + "</p>"
        f'<a class="bot" href="{escape(link)}" rel="nofollow">'
        "Подключить бота</a></div>")


# --- Каркас страницы (палитра живой карты) -----------------------------------

STYLE = """
      :root {
        --bg:#0b0d0d; --panel:#121615; --panel-strong:#181d1b;
        --text:#eef2ec; --muted:#9da8a0; --subtle:#758178;
        --border:rgba(221,230,218,.14); --ok:#75c793; --bad:#e93e4e;
        --bad-soft:#df9a86;
      }
      body { margin:0; background:var(--bg); color:var(--text);
             font:16px/1.6 Inter, ui-sans-serif, system-ui, -apple-system,
             sans-serif; }
      main { max-width:760px; margin:0 auto; padding:36px 20px 72px; }
      h1 { font-size:29px; line-height:1.22; margin:4px 0 22px; }
      h2 { font-size:18px; margin:34px 0 10px; }
      h3 { font-size:15px; margin:20px 0 6px; }
      p { color:var(--muted); }
      strong { color:var(--text); }
      nav.crumbs { font-size:12.5px; color:var(--subtle); }
      nav.crumbs a { color:var(--ok); text-decoration:none; }
      .hero { background:var(--panel-strong); border:1px solid var(--border);
             border-radius:12px; padding:20px 22px; display:flex;
             align-items:center; gap:16px; margin:0 0 20px; }
      .hero.bad { border-color:rgba(223,102,68,.45); }
      .hero.good { border-color:rgba(117,199,147,.4); }
      .hero .dot { width:14px; height:14px; border-radius:50%; flex:none; }
      .hero.bad .dot { background:var(--bad);
                       box-shadow:0 0 0 6px rgba(233,62,78,.15); }
      .hero.good .dot { background:var(--ok);
                        box-shadow:0 0 0 6px rgba(117,199,147,.14); }
      .hero .title { font-size:21px; font-weight:700; line-height:1.3; }
      .hero .sub { font-size:13.5px; color:var(--muted); margin-top:3px; }
      .finder { display:flex; align-items:center; gap:10px;
               background:var(--panel); border:1px solid var(--border);
               border-radius:10px; padding:0 14px; margin:0 0 22px; }
      .finder svg { flex:none; }
      .finder input { flex:1; background:none; border:0; outline:0;
                     color:var(--text); font:inherit; font-size:15px;
                     padding:12px 0; }
      .finder input::placeholder { color:var(--subtle); }
      .band { font-size:12.5px; font-weight:600; letter-spacing:.05em;
             color:var(--subtle); margin:22px 0 10px; }
      .band.bad { color:#df6644; }
      ul.status { list-style:none; margin:0; padding:0; display:grid;
                 grid-template-columns:repeat(2, minmax(0,1fr)); gap:10px; }
      ul.status li { background:var(--panel); border:1px solid var(--border);
                    border-radius:10px; padding:13px 16px; }
      ul.status li.closed { grid-column:1 / -1;
                           background:var(--panel-strong);
                           border-color:rgba(223,102,68,.5);
                           border-left:4px solid var(--bad); }
      .apt-head { display:flex; align-items:center; gap:8px; }
      .apt-dot { width:8px; height:8px; border-radius:50%; flex:none;
                background:var(--ok); }
      li.closed .apt-dot { width:10px; height:10px; background:var(--bad); }
      li.quiet .apt-dot { background:#4b5a52; }
      .apt-name { font-size:15px; font-weight:600; }
      li.closed .apt-name { font-size:17px; font-weight:700; }
      .apt-state { font-size:12.5px; color:var(--muted); margin-top:3px; }
      li.closed .apt-state { font-size:13.5px; color:var(--bad-soft); }
      .apt-hist { font-size:12px; color:var(--subtle); margin-top:2px; }
      ul.status a { font-size:12.5px; color:var(--ok);
                   text-decoration:none; }
      .tiles { display:grid; grid-template-columns:repeat(3, minmax(0,1fr));
              gap:10px; margin:0 0 8px; }
      .tiles div { background:var(--panel); border:1px solid var(--border);
                  border-radius:10px; padding:13px 16px; }
      .tiles b { display:block; font-size:21px; }
      .tiles span { font-size:12.5px; color:var(--muted); }
      .note { background:var(--panel); border:1px solid var(--border);
             border-radius:12px; padding:16px 20px; margin:22px 0; }
      .note h2 { margin:0 0 6px; font-size:15px; }
      .note p { margin:0; font-size:13.5px; line-height:1.55; }
      .cta { border:1px solid rgba(117,199,147,.35); border-radius:12px;
            padding:15px 18px; margin:22px 0; display:flex; flex-wrap:wrap;
            align-items:center; justify-content:space-between; gap:12px; }
      .cta p { margin:0; font-size:13.5px; line-height:1.5; flex:1 1 320px; }
      a.bot { display:inline-block; padding:10px 18px; background:#2aabee;
             color:#fff; text-decoration:none; border-radius:9px;
             font-weight:600; font-size:13.5px; flex:none; }
      table { border-collapse:collapse; width:100%; font-size:13.5px; }
      th, td { text-align:left; padding:10px 14px;
              border-bottom:1px solid rgba(221,230,218,.09); }
      th { color:var(--subtle); font-weight:500; font-size:12.5px; }
      td { color:#c6cfc8; }
      td:last-child, th:last-child { text-align:right; color:var(--muted); }
      .tablecard { background:var(--panel); border:1px solid var(--border);
                  border-radius:10px; overflow:hidden; }
      .tablecard tr:last-child td { border-bottom:0; }
      a.map { display:inline-block; margin:6px 0; padding:12px 20px;
             background:var(--bad); color:#fff; text-decoration:none;
             border-radius:10px; font-weight:600; font-size:14.5px; }
      footer { margin-top:44px; padding-top:18px; font-size:12.5px;
              color:var(--subtle);
              border-top:1px solid rgba(255,255,255,.08); }
      footer a { color:var(--ok); }
      @media (max-width:600px) {
        ul.status { grid-template-columns:1fr; }
        .tiles { grid-template-columns:1fr; }
      }
"""


def head(title: str, description: str, url: str, extra_ld: list[str]) -> str:
    ld = "\n    ".join(
        f'<script type="application/ld+json">{block}</script>'
        for block in extra_ld)
    document_title = f"{title} · Тихое небо"
    if len(document_title) > 70:
        document_title = title
    return f"""<!doctype html>
<html lang="ru">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>{escape(document_title)}</title>
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
    <style>{STYLE}</style>
  </head>
  <body>
    <main>
"""


FOOTER = """
      <footer>
        Неофициальная сводка: собрана по открытым сообщениям, включая ленту
        Росавиации, может опаздывать и ошибаться. Не планируйте по ней
        поездки и рейсы — проверяйте аэропорт, авиакомпанию и оперативные
        службы.
        <br /><a href="/">Карта обстановки</a> ·
        <a href="/aeroporty/">Аэропорты</a> ·
        <a href="/krymskiy-most/">Крымский мост</a> ·
        <a href="/marshruty/">Маршруты БПЛА</a> ·
        <a href="/widget/">Виджет для сайта</a>
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


# --- Разбор ленты Росавиации -------------------------------------------------

def favt_airport_names(header: str) -> list[str]:
    """Аэропорты из шапки сообщения — до глагола ВВЕДЕНЫ/СНЯТЫ.

    Формат гуляет: «Аэропорт ЯРОСЛАВЛЬ (Туношна)», «— ГЕЛЕНДЖИК»,
    «НИЖНЕКАМСК(Бегишево» без пробела и с потерянной скобкой, опечатки
    вроде «ЖУКОВСКЙ». Скобочное имя отбрасывается (ключ — город), а
    незнакомое слово прижимается к ближайшему известному, чтобы опечатка
    регулятора не рождала аэропорт-призрак.
    """
    names = []
    for line in header.split("\n"):
        line = line.split("(")[0]
        line = re.sub(r"[^А-ЯЁа-яё\- ]", " ", line)
        line = re.sub(r"^\s*(Аэропорты|Аэропорт)\b", " ", line).strip(" -")
        line = re.sub(r"\s+", " ", line).strip()
        if not line or not AIRPORT_NAME_RE.fullmatch(line):
            continue
        if line not in KNOWN_KEYS:
            near = difflib.get_close_matches(line, KNOWN_KEYS, n=1, cutoff=0.8)
            line = near[0] if near else line
        names.append(line)
    return names


def official_transitions(connection: sqlite3.Connection,
                         since: str) -> list[tuple[str, str, str]]:
    """(время, аэропорт, close|open) из сообщений Росавиации, по порядку."""
    rows = connection.execute(
        f"""SELECT posted_at, text FROM raw_messages
            WHERE source_key IN ({','.join('?' for _ in FAVT_SOURCES)})
              AND posted_at >= ?
              AND (text LIKE '%ВВЕДЕНЫ%' OR text LIKE '%СНЯТЫ%')
            ORDER BY posted_at""", (*FAVT_SOURCES, since)).fetchall()
    transitions: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in rows:
        text = row["text"] or ""
        closes, opens = "ВВЕДЕНЫ" in text, "СНЯТЫ" in text
        if closes == opens:
            continue
        action = "close" if closes else "open"
        header = re.split(r"ВВЕДЕНЫ|СНЯТЫ", text)[0]
        for key in favt_airport_names(header):
            item = (row["posted_at"], key, action)
            # Канал ведётся под двумя ключами источника — каждое сообщение
            # приходит дважды.
            if item in seen:
                continue
            seen.add(item)
            transitions.append(item)
    return transitions


def airport_rows(connection: sqlite3.Connection) -> list[dict]:
    """Статус и месячная история каждого аэропорта — по словам регулятора."""
    since = (now_utc() - timedelta(days=WINDOW_DAYS)).isoformat()
    registry = {key: (name, city, slug, zones)
                for key, name, city, slug, zones in AIRPORTS}
    closed_since: dict[str, str | None] = {}
    reopened_at: dict[str, str] = {}
    pairs: dict[str, list[float]] = {}
    closures: dict[str, int] = {}
    for at, key, action in official_transitions(connection, since):
        if action == "close":
            if not closed_since.get(key):
                closed_since[key] = at
                closures[key] = closures.get(key, 0) + 1
        elif closed_since.get(key):
            minutes = (datetime.fromisoformat(at)
                       - datetime.fromisoformat(closed_since[key])
                       ).total_seconds() / 60
            if 1 <= minutes <= 2880:
                pairs.setdefault(key, []).append(minutes)
            closed_since[key] = None
            reopened_at[key] = at
        else:
            reopened_at[key] = at

    rows = []
    now = now_utc()
    for key in {*registry, *closures, *reopened_at}:
        name, city, slug, zones = registry.get(
            key, (key.title(), key.title(), None, ()))
        since_at = closed_since.get(key)
        closed = bool(since_at) and (
            now - datetime.fromisoformat(since_at)
        ) <= timedelta(hours=STALE_CLOSE_HOURS)
        durations = pairs.get(key, [])
        rows.append({
            "key": key, "name": name, "city": city, "region_slug": slug,
            "zone_ids": zones, "closed": closed,
            "since": since_at if closed else None,
            "reopened": reopened_at.get(key) if not closed else None,
            "closures": closures.get(key, 0),
            "median_minutes": (int(median(durations)) if durations else None),
        })
    rows.sort(key=lambda r: (not r["closed"], -r["closures"], r["name"]))
    return rows


# --- Страница аэропортов -----------------------------------------------------

def airport_card(row: dict) -> str:
    classes = ("closed" if row["closed"]
               else "open" if row["closures"] else "quiet")
    # Оба варианта текста уезжают в data-атрибуты: живой скрипт двигает
    # карточку между «закрыты» и «работают» и обязан переписать и статус,
    # и строку истории — полкарточки от одного состояния, полкарточки от
    # другого читались как «Закрыт… Работает» одновременно.
    state_closed = (
        f"Закрыт {since_clock(row['since'])} — самолёты не взлетают "
        f"и не садятся" if row["closed"]
        else "Закрыт — действуют ограничения")
    if row["reopened"]:
        state_open = f"Работает — открыли {clock(row['reopened'])}"
    elif row["closures"]:
        state_open = "Работает"
    else:
        state_open = "Работает — за месяц не закрывался"
    hist_closed = (f"Обычно открывают примерно через "
                   f"{minutes_word(row['median_minutes'])}"
                   if row["median_minutes"] else "")
    if row["closures"]:
        typical = (f", обычно на {minutes_word(row['median_minutes'])}"
                   if row["median_minutes"] else "")
        hist_open = (f"{row['closures']} "
                     f"{plural(row['closures'], 'закрытие', 'закрытия', 'закрытий')}"
                     f" за месяц{typical}")
    else:
        hist_open = "За месяц не закрывался"
    state = state_closed if row["closed"] else state_open
    hist = hist_closed if row["closed"] else hist_open
    link = (f'<a href="/region/{row["region_slug"]}/">Обстановка — '
            f"{escape(row['city'])}</a>" if row["region_slug"] else "")
    zones = escape(json.dumps(row["zone_ids"]), quote=True)
    query = escape(f"{row['name']} {row['city']}".lower(), quote=True)
    return (f'<li class="{classes}" data-zones=\'{zones}\' data-q="{query}" '
            f'data-static="{"closed" if row["closed"] else "open"}" '
            f'data-state-closed="{escape(state_closed, quote=True)}" '
            f'data-state-open="{escape(state_open, quote=True)}" '
            f'data-hist-closed="{escape(hist_closed, quote=True)}" '
            f'data-hist-open="{escape(hist_open, quote=True)}">'
            f'<div class="apt-head"><span class="apt-dot"></span>'
            f'<span class="apt-name">{escape(row["name"])}</span></div>'
            f'<div class="apt-state">{escape(state)}</div>'
            + (f'<div class="apt-hist">{escape(hist)}</div>' if hist else "")
            + link + "</li>")


PAGE_JS = """
<script>
(function () {
  var finder = document.getElementById("apt-finder");
  var cards = Array.prototype.slice.call(
    document.querySelectorAll("#airports li"));
  var bands = Array.prototype.slice.call(
    document.querySelectorAll(".band"));
  var closedList = document.getElementById("airports-closed");
  var openList = document.getElementById("airports-open");
  var closedBand = document.getElementById("band-closed");
  var hero = document.getElementById("hero");
  var heroTitle = document.getElementById("hero-title");
  if (finder) finder.addEventListener("input", function () {
    var query = finder.value.trim().toLowerCase().replace(/ё/g, "е");
    cards.forEach(function (card) {
      var match = !query ||
        card.dataset.q.replace(/ё/g, "е").indexOf(query) !== -1;
      card.style.display = match ? "" : "none";
    });
    bands.forEach(function (band) { band.style.display = query ? "none" : ""; });
  });

  function plural(n, one, few, many) {
    var t = n % 10, p = n % 100;
    if (t === 1 && p !== 11) return one;
    if (t >= 2 && t <= 4 && (p < 12 || p > 14)) return few;
    return many;
  }

  function setCard(card, closed) {
    if (closed === card.classList.contains("closed")) return;
    card.classList.toggle("closed", closed);
    card.classList.toggle("open", !closed);
    card.classList.remove("quiet");
    var state = card.querySelector(".apt-state");
    if (state) state.textContent = closed
      ? card.dataset.stateClosed : card.dataset.stateOpen;
    var hist = card.querySelector(".apt-hist");
    if (hist) hist.textContent = closed
      ? card.dataset.histClosed : card.dataset.histOpen;
    (closed ? closedList : openList).appendChild(card);
  }

  function redraw() {
    var closed = cards.filter(function (card) {
      return card.classList.contains("closed");
    }).length;
    if (closedBand) closedBand.style.display = closed ? "" : "none";
    if (!hero || !heroTitle) return;
    hero.className = "hero " + (closed ? "bad" : "good");
    heroTitle.textContent = closed
      ? "Сейчас " + plural(closed, "закрыт", "закрыты", "закрыты") + " " +
        closed + " " + plural(closed, "аэропорт", "аэропорта", "аэропортов") +
        " из " + cards.length
      : "Все аэропорты работают";
  }

  function refresh() {
    fetch("/api/v1/state").then(function (r) { return r.json(); })
      .then(function (state) {
        // Живые события умеют только два утверждения: свежее закрытие
        // (активное событие) и свежее открытие (недавний отбой). Тишина в
        // эфире НЕ значит «работает»: событие на карте гаснет по времени
        // раньше, чем Росавиация присылает «СНЯТЫ», — карточка без
        // живого сигнала возвращается к официальной статике.
        var nowClosed = {}, nowOpened = {};
        (state.events || []).forEach(function (event) {
          if (event.threat_type !== "airport") return;
          var zones = (event.zone_path || []).concat([event.zone_id]);
          var bucket = event.status === "resolved" ? nowOpened : nowClosed;
          zones.forEach(function (zone) { bucket[zone] = true; });
        });
        cards.forEach(function (card) {
          if (!card.dataset.zones) return;
          var zones = JSON.parse(card.dataset.zones);
          if (zones.some(function (z) { return nowClosed[z]; })) {
            setCard(card, true);
          } else if (zones.some(function (z) { return nowOpened[z]; })) {
            setCard(card, false);
          } else {
            setCard(card, card.dataset.static === "closed");
          }
        });
        redraw();
      }).catch(function () {});
  }
  refresh();
  setInterval(refresh, 60000);
})();
</script>
"""

SEARCH_ICON = ('<svg width="18" height="18" viewBox="0 0 24 24" fill="none" '
               'stroke="#758178" stroke-width="2" stroke-linecap="round" '
               'aria-hidden="true"><circle cx="11" cy="11" r="7"></circle>'
               '<path d="m20 20-3.2-3.2"></path></svg>')


def airports_page(rows: list[dict], updated: str) -> str:
    closed = [row for row in rows if row["closed"]]
    working = [row for row in rows if not row["closed"]]
    url = f"{SITE}/aeroporty/"
    title = "Какие аэропорты закрыты сейчас — список Росавиации онлайн"
    if closed:
        names = ", ".join(row["name"] for row in closed[:4])
        description = (
            f"Сейчас закрыто: {names}"
            + (" и другие" if len(closed) > 4 else "")
            + f". Статус {len(rows)} аэропортов по официальным сообщениям "
              f"Росавиации, история закрытий за 30 дней, поиск.")
    else:
        description = (
            f"Сейчас закрытых аэропортов нет. Статус {len(rows)} аэропортов "
            f"по официальным сообщениям Росавиации онлайн, история "
            f"ограничений за 30 дней, поиск.")

    if closed:
        hero_class, hero_title = "bad", (
            f"Сейчас закрыт{'ы' if len(closed) > 1 else ''} "
            f"{len(closed)} {plural(len(closed), 'аэропорт', 'аэропорта', 'аэропортов')} из {len(rows)}")
    else:
        hero_class, hero_title = "good", "Все аэропорты работают"
    hero = (
        f'<div class="hero {hero_class}" id="hero"><span class="dot"></span>'
        f'<div><div class="title" id="hero-title">{escape(hero_title)}</div>'
        f'<div class="sub">По официальным сообщениям Росавиации · '
        f"обновлено {updated}</div></div></div>")

    all_medians = [row["median_minutes"] for row in rows
                   if row["median_minutes"]]
    typical = int(median(all_medians)) if all_medians else None
    total = sum(row["closures"] for row in rows)
    faq = [
        ("Почему закрывают аэропорты",
         "При угрозе БПЛА Росавиация вводит план «Ковёр» — временные "
         "ограничения на приём и выпуск воздушных судов. Рейсы ждут на "
         "земле или уходят на запасные аэродромы, пока угрозу не снимут."),
        ("Как долго аэропорт остаётся закрытым",
         (f"За последние 30 дней — {total} "
          f"{plural(total, 'закрытие', 'закрытия', 'закрытий')}; типичное "
          f"длится около {minutes_word(typical)}, но бывают и многочасовые "
          f"— всё зависит от обстановки."
          if typical else
          "За последние 30 дней закрытий не было.")),
        ("Аэропорт открыли, а рейса всё нет — почему",
         "После снятия ограничений расписание восстанавливается ещё "
         "несколько часов: борта возвращаются с запасных аэродромов, "
         "экипажи выходят за пределы рабочего времени. Задержки после "
         "открытия — обычное дело, проверяйте статус рейса у авиакомпании."),
        ("Откуда данные и насколько они точны",
         "Статусы собраны по официальным сообщениям Росавиации в её "
         "Telegram-ленте, которые карта читает вместе с остальными "
         "источниками. Сводка неофициальная и может опаздывать: статус "
         "рейса проверяйте на сайте аэропорта или у авиакомпании."),
    ]
    # Обе секции есть всегда: живой скрипт двигает карточки между ними,
    # пустая «закрытая» просто спрятана.
    hidden = "" if closed else ' style="display:none"'
    closed_html = (
        f'<div id="band-closed"{hidden}>'
        '<div class="band bad">ЗАКРЫТЫ</div>'
        '<ul class="status" id="airports-closed">'
        + "".join(airport_card(row) for row in closed)
        + "</ul></div>")
    working_html = ('<div class="band">РАБОТАЮТ</div>'
                    '<ul class="status" id="airports-open">'
                    + "".join(airport_card(row) for row in working)
                    + "</ul>")
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
    return (
        head(title, description, url, ld)
        + f"""      <nav class="crumbs"><a href="/">Карта обстановки</a> → Аэропорты</nav>
      <h1>Какие аэропорты сейчас закрыты</h1>
      {hero}
      <div class="finder">{SEARCH_ICON}<input id="apt-finder" type="search"
        placeholder="Найти аэропорт или город — например, Сочи"
        aria-label="Поиск аэропорта" /></div>
      <div id="airports">
      {closed_html}
      {working_html}
      </div>
      <div class="note">
        <h2>Что значит «аэропорт закрыт»</h2>
        <p>При угрозе БПЛА Росавиация останавливает приём и выпуск
        самолётов — это называют планом «Ковёр». Рейсы ждут на земле или
        уходят на запасные аэродромы. После открытия расписание догоняет
        себя ещё несколько часов, поэтому статус рейса проверяйте у
        авиакомпании.</p>
      </div>
      {bot_cta(None, "Летите на днях?",
               "Бот напишет в Telegram, когда ваш аэропорт закроют "
               "или откроют.")}
      <a class="map" href="/">Открыть карту обстановки</a>
      {faq_html(faq)}
      <p>Сводка обновлена {updated}; карточки дообновляются в браузере раз
      в минуту.</p>{FOOTER}{PAGE_JS}""")


# --- Крымский мост -----------------------------------------------------------

def bridge_timeline(connection: sqlite3.Connection) -> list[dict]:
    """Перекрытия и открытия моста из самих сообщений каналов."""
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


def daypart(closures: list[dict]) -> str | None:
    """Когда перекрывают чаще: ночью, утром, днём или вечером (МСК)."""
    if len(closures) < 4:
        return None
    parts = {"ночью": 0, "утром": 0, "днём": 0, "вечером": 0}
    for item in closures:
        hour = datetime.fromisoformat(item["from"]).astimezone(MSK).hour
        name = ("ночью" if hour < 6 else "утром" if hour < 12
                else "днём" if hour < 18 else "вечером")
        parts[name] += 1
    best = max(parts, key=parts.get)
    return best if parts[best] > len(closures) / 3 else None


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
    typical = (int(median([c["minutes"] for c in closures]))
               if closures else None)
    busiest = daypart(closures)

    title = "Крымский мост сейчас — открыт или закрыт, обстановка онлайн"
    if closed_now and since:
        description = (f"Движение по Крымскому мосту перекрыто "
                       f"{since_clock(since)} МСК. Сколько обычно длится "
                       f"перекрытие и история за 30 дней — онлайн.")
    else:
        description = (
            "Движение по Крымскому мосту сейчас открыто. "
            + (f"За месяц {len(closures)} "
               + plural(len(closures), "перекрытие", "перекрытия",
                        "перекрытий")
               + (f", обычно на {minutes_word(typical)}" if typical else "")
               + ". " if closures else "")
            + "Статус и история перекрытий по живым сообщениям.")

    if closed_now:
        hero = (
            '<div class="hero bad"><span class="dot"></span><div>'
            '<div class="title">Движение перекрыто</div>'
            f'<div class="sub">Перекрыли {clock(since)} МСК · по сообщениям '
            "оперативных каналов</div></div></div>")
    else:
        last_open = steps[-1]["at"] if steps else None
        hero = (
            '<div class="hero good"><span class="dot"></span><div>'
            '<div class="title">Движение открыто</div>'
            f'<div class="sub">'
            + (f"Возобновили {clock(last_open)} МСК · " if last_open else "")
            + "по сообщениям оперативных каналов</div></div></div>")

    tiles = ""
    if closures:
        tiles = (
            '<div class="tiles">'
            f"<div><b>{len(closures)}</b><span>"
            f"{plural(len(closures), 'перекрытие', 'перекрытия', 'перекрытий')}"
            " за месяц</span></div>"
            + (f"<div><b>~{minutes_word(typical)}</b><span>длится обычное "
               "перекрытие</span></div>" if typical else "")
            + (f"<div><b>{busiest}</b><span>перекрывают чаще всего</span>"
               "</div>" if busiest else "")
            + "</div>")

    history_rows = "".join(
        f"<tr><td>{span_text(c['from'], c['to'])}</td>"
        f"<td>{minutes_word(c['minutes'])}</td></tr>"
        for c in closures[:15])
    history = (
        "<h2>Последние перекрытия</h2>\n"
        '<div class="tablecard"><table>'
        "<tr><th>Когда (МСК)</th><th>Длилось</th></tr>"
        f"{history_rows}</table></div>" if closures else
        "<h2>Перекрытия за 30 дней</h2>\n<p>За последний месяц сообщений о "
        "перекрытии моста не было.</p>")

    faq = [
        ("Почему перекрывают Крымский мост",
         "Движение останавливают при угрозе БПЛА или безэкипажных катеров "
         "в Керченском проливе. Это профилактика: перекрытие само по себе "
         "не означает атаку на мост."),
        ("Сколько обычно длится перекрытие",
         (f"За последние 30 дней — {len(closures)} "
          f"{plural(len(closures), 'перекрытие', 'перекрытия', 'перекрытий')}, "
          f"обычно около {minutes_word(typical)}. Отдельные длятся дольше — "
          f"зависит от обстановки."
          if typical else
          "За последние 30 дней перекрытий не фиксировалось.")),
        ("Что делать, если вы на мосту или в очереди",
         "Следуйте указаниям сотрудников на месте: обычно просят оставаться "
         "в машине или пройти в укрытие на досмотровых пунктах. Движение "
         "чаще всего открывают в пределах часа."),
        ("Откуда данные",
         "Открытые сообщения оперативных Telegram-каналов Крыма и Кубани. "
         "Сводка неофициальная: перед поездкой проверяйте состояние моста "
         "по официальным источникам."),
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
    return (
        head(title, description, url, ld)
        + f"""      <nav class="crumbs"><a href="/">Карта обстановки</a> → Крымский мост</nav>
      <h1>Крымский мост сейчас</h1>
      {hero}
      {tiles}
      {history}
      <div class="note">
        <h2>Если вы едете к мосту</h2>
        <p>Перекрытие — профилактика при угрозе с воздуха или воды, а не
        сообщение об атаке. Оставайтесь в машине или пройдите в укрытие на
        досмотровых пунктах — как скажут на месте. Обычно движение
        открывают в пределах часа.</p>
      </div>
      {bot_cta(KERCH_ZONE, "Едете в Крым?",
               "Бот напишет в Telegram, когда по Керчи объявят тревогу "
               "или дадут отбой.")}
      <a class="map" href="/?region=respublika-krym">Открыть карту — Крым</a>
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
