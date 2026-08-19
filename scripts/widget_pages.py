"""Встраиваемый виджет обстановки: карточка региона для чужих сайтов.

Городские порталы и региональные сообщества пересказывают обстановку
руками. Виджет отдаёт им живую карточку одним iframe — а карте даёт
обратные ссылки, самый честный сигнал для поисковика.

Каждому региону собирается лёгкая страница /widget/<слаг>/ с вшитым
именем и месячной цифрой; текущее состояние карточка добирает сама из
/api/v1/state раз в минуту. Витрина /widget/ показывает предпросмотр и
даёт готовый код для вставки.
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
      body {{ background:#0e1413; color:#e6ebe6; display:flex;
             font:14px/1.45 Inter, system-ui, -apple-system, sans-serif; }}
      a.card {{ flex:1; display:flex; flex-direction:column; gap:4px;
               justify-content:center; padding:12px 16px;
               color:inherit; text-decoration:none;
               border:1px solid rgba(255,255,255,.1); border-radius:10px;
               margin:4px; }}
      .top {{ display:flex; align-items:center; gap:8px; font-weight:600;
             font-size:15px; }}
      .dot {{ width:9px; height:9px; border-radius:50%; flex:none; }}
      .dot.calm {{ background:#5f6f66; }}
      .dot.warm {{ background:#e8b34b; }}
      .dot.hot {{ background:#e93e4e; }}
      #line {{ color:#aab4ad; }}
      .brand {{ font-size:12px; color:#7d8a83; }}
      .brand b {{ color:#9fd4b0; font-weight:600; }}
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
    """Витрина /widget/: предпросмотр и код для вставки."""
    options = "".join(
        f'<option value="{slug}">{escape(name)}</option>'
        for name, slug in named)
    url = f"{SITE}/widget/"
    title = "Виджет воздушной обстановки для сайта — бесплатно"
    description = (
        "Живая карточка обстановки региона для вашего сайта: тревоги, "
        "БПЛА и отбои, обновляется каждую минуту. Один iframe, без "
        "скриптов и регистрации.")
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
      body {{ margin:0; background:#0b0f0e; color:#e6ebe6;
             font:16px/1.6 Inter, system-ui, -apple-system, sans-serif; }}
      main {{ max-width:760px; margin:0 auto; padding:40px 20px 80px; }}
      h1 {{ font-size:29px; line-height:1.25; margin:0 0 14px; }}
      h2 {{ font-size:19px; margin:34px 0 10px; }}
      p {{ color:#aab4ad; }}
      nav.crumbs {{ font-size:13px; color:#7d8a83; margin:0 0 18px; }}
      nav.crumbs a {{ color:#9fd4b0; text-decoration:none; }}
      select {{ background:#141b19; color:#e6ebe6; font:inherit;
               border:1px solid rgba(255,255,255,.15); border-radius:8px;
               padding:9px 12px; margin:8px 0 18px; max-width:100%; }}
      iframe {{ border:0; width:100%; max-width:340px; height:110px;
               border-radius:10px; }}
      textarea {{ width:100%; box-sizing:border-box; height:96px;
                 background:#141b19; color:#c6cfc8; font:13px/1.5 ui-monospace,
                 monospace; border:1px solid rgba(255,255,255,.15);
                 border-radius:8px; padding:10px; }}
      footer {{ margin-top:44px; padding-top:18px; font-size:13px;
               color:#7d8a83; border-top:1px solid rgba(255,255,255,.08); }}
      footer a {{ color:#9fd4b0; }}
    </style>
  </head>
  <body>
    <main>
      <nav class="crumbs"><a href="/">Карта обстановки</a> → Виджет</nav>
      <h1>Виджет обстановки для вашего сайта</h1>
      <p>Живая карточка региона: тревоги, БПЛА и отбои, обновляется каждую
      минуту из тех же данных, что и <a href="/" style="color:#9fd4b0">
      карта</a>. Один iframe — без скриптов, регистрации и платы.
      Карточка ведёт на сводку региона.</p>

      <h2>Выберите регион</h2>
      <select id="region">{options}</select>
      <div><iframe id="preview" loading="lazy" title="Виджет обстановки"
        src="/widget/belgorodskaya-oblast/"></iframe></div>

      <h2>Код для вставки</h2>
      <textarea id="code" readonly></textarea>
      <p>Высоту и ширину можно менять под свою колонку — карточка
      растягивается. Тёмная сама по себе; на светлом сайте смотрится как
      врезка.</p>

      <footer>
        Обновлено {updated}. Данные — открытые сообщения, карта
        неофициальная.
        <br /><a href="/">Карта обстановки</a> ·
        <a href="/aeroporty/">Аэропорты</a> ·
        <a href="/marshruty/">Маршруты БПЛА</a>
      </footer>
    </main>
    <script>
      var select = document.getElementById("region");
      var frame = document.getElementById("preview");
      var code = document.getElementById("code");
      function update() {{
        var slug = select.value;
        frame.src = "/widget/" + slug + "/";
        code.value = '<iframe src="{SITE}/widget/' + slug +
          '/" width="320" height="110" frameborder="0" ' +
          'title="Воздушная обстановка"><\\/iframe>';
      }}
      select.value = "belgorodskaya-oblast";
      select.addEventListener("change", update);
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
