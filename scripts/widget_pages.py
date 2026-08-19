"""Встраиваемый виджет обстановки: карточка региона для чужих сайтов.

Городские порталы и региональные сообщества пересказывают обстановку
руками. Виджет отдаёт им живую карточку одним iframe — а карте даёт
обратные ссылки, самый честный сигнал для поисковика.

Каждому региону собирается лёгкая страница /widget/<слаг>/ с вшитым
именем и месячной цифрой; текущее состояние карточка добирает сама из
/api/v1/state раз в минуту. Витрина /widget/ ведёт человека за три шага:
выбрать регион, скопировать код, вставить.

Дизайн-макеты: артефакт «Статусные страницы Тихого неба»; палитра — с
живой карты (src/styles.css).
"""

from __future__ import annotations

import sys
from html import escape
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.db import ROOT

SITE = "https://tihoenebo.com"
OUT = ROOT / "dist"

EMBED_JS = """
<script>
(function () {
  var zone = document.body.dataset.zone;
  function refresh() {
    fetch("/api/v1/state").then(function (r) { return r.json(); })
      .then(function (state) {
        var fresh = (state.events || []).filter(function (event) {
          if (event.status === "resolved") return false;
          var path = event.zone_path || [];
          return path[path.length - 1] === zone || event.zone_id === zone;
        });
        var dot = document.getElementById("dot");
        var line = document.getElementById("line");
        if (!fresh.length) {
          dot.className = "dot calm";
          line.textContent = "сейчас тихо — активных событий нет";
          return;
        }
        var worst = 0, last = null;
        fresh.forEach(function (event) {
          if (event.severity > worst) worst = event.severity;
          if (!last || event.first_seen_at > last.first_seen_at) last = event;
        });
        dot.className = "dot " + (worst >= 8 ? "hot" : "warm");
        var what = {detection: "фиксация БПЛА", intercept: "работа ПВО",
                    impact: "взрыв", alarm: "воздушная тревога",
                    danger: "опасность", infra: "инфраструктура"}[
                    last.signal_type] || "событие";
        var n = fresh.length, tail = n % 10, pair = n % 100;
        var word = (tail === 1 && pair !== 11) ? "активное событие"
          : (tail >= 2 && tail <= 4 && (pair < 12 || pair > 14))
            ? "активных события" : "активных событий";
        line.textContent = n + " " + word +
          " · " + what + (last.place_name ? " — " + last.place_name : "");
      }).catch(function () {});
  }
  refresh();
  setInterval(refresh, 60000);
})();
</script>
"""


def embed_page(name: str, slug: str, zone_id: str, month_events: int) -> str:
    month = (f"{month_events} событий за 30 дней" if month_events
             else "месяц без сообщений об опасности")
    return f"""<!doctype html>
<html lang="ru">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <meta name="robots" content="noindex" />
    <title>Обстановка — {escape(name)}</title>
    <style>
      html, body {{ margin:0; height:100%; }}
      body {{ background:#0e1413; color:#eef2ec; display:flex;
             font:14px/1.45 Inter, ui-sans-serif, system-ui, sans-serif; }}
      a.card {{ flex:1; display:flex; flex-direction:column; gap:4px;
               justify-content:center; padding:12px 16px;
               color:inherit; text-decoration:none;
               border:1px solid rgba(221,230,218,.14); border-radius:10px;
               margin:4px; }}
      .top {{ display:flex; align-items:center; gap:8px; font-weight:600;
             font-size:15px; }}
      .dot {{ width:9px; height:9px; border-radius:50%; flex:none; }}
      .dot.calm {{ background:#5f6f66; }}
      .dot.warm {{ background:#f2b765; }}
      .dot.hot {{ background:#e93e4e; }}
      #line {{ color:#9da8a0; }}
      .brand {{ font-size:12px; color:#758178; }}
      .brand b {{ color:#75c793; font-weight:600; }}
    </style>
  </head>
  <body data-zone="{escape(zone_id, quote=True)}">
    <a class="card" href="{SITE}/region/{slug}/" target="_blank"
       rel="noopener">
      <span class="top"><span class="dot calm" id="dot"></span>
        {escape(name)}</span>
      <span id="line">{escape(month)}</span>
      <span class="brand">карта обстановки — <b>тихое небо</b></span>
    </a>
    {EMBED_JS}
  </body>
</html>
"""


def promo_page(named: list[tuple[str, str]], updated: str) -> str:
    """Витрина /widget/: три шага, предпросмотр, код для вставки."""
    options = "".join(
        f'<option value="{slug}">{escape(name)}</option>'
        for name, slug in named)
    url = f"{SITE}/widget/"
    title = "Виджет воздушной обстановки для сайта — бесплатно"
    description = (
        "Живая карточка обстановки региона для вашего сайта: тревоги, "
        "БПЛА и отбои, обновляется каждую минуту. Один iframe, без "
        "скриптов и регистрации.")
    steps = "".join(
        f'<div class="step"><span class="n">{i}</span>{escape(text)}</div>'
        for i, text in ((1, "Выберите регион"), (2, "Скопируйте код"),
                        (3, "Вставьте на сайт")))
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
    <meta property="og:url" content="{url}" />
    <meta property="og:title" content="{escape(title)}" />
    <meta property="og:description" content="{escape(description)}" />
    <meta property="og:image" content="{SITE}/preview.png" />
    <meta name="theme-color" content="#0e1211" />
    <style>
      body {{ margin:0; background:#0b0d0d; color:#eef2ec;
             font:16px/1.6 Inter, ui-sans-serif, system-ui, sans-serif; }}
      main {{ max-width:760px; margin:0 auto; padding:36px 20px 72px; }}
      h1 {{ font-size:29px; line-height:1.22; margin:4px 0 10px; }}
      p {{ color:#9da8a0; }}
      nav.crumbs {{ font-size:12.5px; color:#758178; }}
      nav.crumbs a {{ color:#75c793; text-decoration:none; }}
      .steps {{ display:grid; grid-template-columns:repeat(3, minmax(0,1fr));
               gap:10px; margin:22px 0; }}
      .step {{ display:flex; align-items:center; gap:10px; font-size:13px;
              color:#9da8a0; }}
      .step .n {{ width:26px; height:26px; border-radius:50%;
                 background:#1c2422; color:#f2b765; font-size:13px;
                 font-weight:700; display:flex; align-items:center;
                 justify-content:center; flex:none; }}
      .cols {{ display:grid; grid-template-columns:repeat(2, minmax(0,1fr));
              gap:20px; align-items:start; }}
      .label {{ font-size:12.5px; font-weight:600; letter-spacing:.05em;
               color:#758178; margin:0 0 8px; }}
      select {{ width:100%; box-sizing:border-box; background:#121615;
               color:#eef2ec; font:inherit; font-size:14.5px;
               border:1px solid rgba(221,230,218,.22); border-radius:9px;
               padding:10px 12px; margin:0 0 16px; }}
      iframe {{ border:0; width:100%; height:110px; border-radius:10px; }}
      textarea {{ width:100%; box-sizing:border-box; height:92px;
                 background:#121615; color:#9da8a0;
                 font:12px/1.6 ui-monospace, monospace;
                 border:1px solid rgba(221,230,218,.14); border-radius:9px;
                 padding:10px 12px; resize:none; }}
      button.copy {{ width:100%; margin-top:10px; padding:11px 18px;
                    background:#75c793; color:#0b0d0d; font:inherit;
                    font-size:13.5px; font-weight:700; border:0;
                    border-radius:9px; cursor:pointer; }}
      button.copy:hover {{ background:#8fd8a9; }}
      .hint {{ font-size:12.5px; color:#758178; line-height:1.55;
              margin-top:10px; }}
      footer {{ margin-top:44px; padding-top:18px; font-size:12.5px;
               color:#758178; border-top:1px solid rgba(255,255,255,.08); }}
      footer a {{ color:#75c793; }}
      @media (max-width:600px) {{
        .steps, .cols {{ grid-template-columns:1fr; }}
      }}
    </style>
  </head>
  <body>
    <main>
      <nav class="crumbs"><a href="/">Карта обстановки</a> → Виджет</nav>
      <h1>Обстановка региона — на вашем сайте</h1>
      <p>Живая карточка: тревоги, БПЛА и отбои, обновляется каждую минуту
      из тех же данных, что и карта. Вставляется одной строкой, без
      регистрации и платы. Клик по карточке ведёт на сводку региона.</p>

      <div class="steps">{steps}</div>

      <div class="cols">
        <div>
          <div class="label">РЕГИОН</div>
          <select id="region">{options}</select>
          <div class="label">КОД ДЛЯ ВСТАВКИ</div>
          <textarea id="code" readonly></textarea>
          <button type="button" class="copy" id="copy">Скопировать код</button>
        </div>
        <div>
          <div class="label">ТАК ЭТО ВЫГЛЯДИТ</div>
          <iframe id="preview" loading="lazy" title="Виджет обстановки"
            src="/widget/belgorodskaya-oblast/"></iframe>
          <div class="hint">Тёмная карточка сама по себе; на светлом сайте
          смотрится как врезка. Ширину и высоту можно менять под свою
          колонку.</div>
        </div>
      </div>

      <footer>
        Обновлено {updated}. Данные — открытые сообщения, карта
        неофициальная.
        <br /><a href="/">Карта обстановки</a> ·
        <a href="/aeroporty/">Аэропорты</a> ·
        <a href="/krymskiy-most/">Крымский мост</a> ·
        <a href="/marshruty/">Маршруты БПЛА</a>
      </footer>
    </main>
    <script>
      var select = document.getElementById("region");
      var frame = document.getElementById("preview");
      var code = document.getElementById("code");
      var copy = document.getElementById("copy");
      function update() {{
        var slug = select.value;
        frame.src = "/widget/" + slug + "/";
        code.value = '<iframe src="{SITE}/widget/' + slug +
          '/" width="320" height="110" frameborder="0" ' +
          'title="Воздушная обстановка"><\\/iframe>';
      }}
      select.value = "belgorodskaya-oblast";
      select.addEventListener("change", update);
      copy.addEventListener("click", function () {{
        code.select();
        var done = function () {{
          copy.textContent = "Скопировано";
          setTimeout(function () {{
            copy.textContent = "Скопировать код";
          }}, 1600);
        }};
        if (navigator.clipboard) {{
          navigator.clipboard.writeText(code.value).then(done);
        }} else {{
          document.execCommand("copy");
          done();
        }}
      }});
      update();
    </script>
  </body>
</html>
"""


def build(named: list[tuple[str, str, str, int]], updated: str) -> list[str]:
    """named: (имя, слаг, zone_id, событий за месяц). Возвращает URL для
    sitemap — витрину; сами embed-страницы стоят в noindex."""
    for name, slug, zone_id, month_events in named:
        directory = OUT / "widget" / slug
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "index.html").write_text(
            embed_page(name, slug, zone_id, month_events), encoding="utf-8")
    (OUT / "widget" / "index.html").write_text(
        promo_page([(name, slug) for name, slug, _, _ in named], updated),
        encoding="utf-8")
    print(f"Виджет: {len(named)} регионов и витрина")
    return [f"{SITE}/widget/"]
