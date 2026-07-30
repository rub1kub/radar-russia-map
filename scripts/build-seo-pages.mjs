/**
 * Посадочные страницы по регионам.
 *
 * Карта — одностраничное приложение: у неё один URL и пустой div для
 * робота. Поисковику нечего показать по запросу «тревога в Белгородской
 * области», хотя именно так и ищут. Здесь для каждого из 89 субъектов
 * собирается отдельная статическая страница: свой заголовок, описание,
 * перечень районов и ссылка на карту с уже выбранным регионом.
 *
 * Содержание настоящее, а не набивка ключевыми словами: список районов
 * помогает человеку найти свой и заодно делает каждую страницу
 * непохожей на остальные — иначе поисковик считает их дублями.
 *
 * Запускается сборкой (см. package.json), результат ложится в dist/.
 */

import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(fileURLToPath(new URL("../package.json", import.meta.url)));
const SITE = process.env.VITE_API_BASE || "https://tihoenebo.com";
const outRoot = join(root, "dist");

const regions = JSON.parse(readFileSync(join(root, "public/data/regions.json"), "utf8"));
const districts = JSON.parse(readFileSync(join(root, "public/data/districts.json"), "utf8"));

/** Латиница для адреса: подчёркивания справочника — в дефисы. */
const slugOf = (zone) => String(zone || "").replace(/_/g, "-");

const escape = (s) =>
  String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

// Районы по региону: связь лежит в самом полигоне (свойство region).
const byRegion = new Map();
for (const feature of districts.features) {
  const p = feature.properties || {};
  if (!p.region || !p.name) continue;
  if (!byRegion.has(p.region)) byRegion.set(p.region, []);
  byRegion.get(p.region).push(p.name);
}

/**
 * Предложный падеж названия субъекта: «в Курской области», а не «в Курская
 * область». Именительный в заголовке читается как машинный перевод и сразу
 * выдаёт штампованную страницу — ради этого стоит потрудиться.
 *
 * Правила покрывают все формы, что есть в справочнике: «...ая область/
 * республика», «...ий край/округ/автономный округ», города федерального
 * значения и односложные республики («Адыгея», «Чувашия», «Крым»).
 */
const inflect = (name) => {
  const rules = [
    // Составные с родовым словом впереди: склоняется слово, имя стоит.
    [/^Республика (.+)$/i, "Республике $1"],
    [/^Чеченская Республика$/i, "Чеченской Республике"],
    // Составные разбираются раньше простых: «Еврейская автономная область»
    // иначе попадала под правило «...ая область» и теряла середину.
    [/^(.+)ая автономная область$/i, "$1ой автономной области"],
    [/^(.+)ая народная республика$/i, "$1ой народной республике"],
    [/^(.+)ая республика$/i, "$1ой республике"],
    [/^(.+)ая область$/i, "$1ой области"],
    [/^(.+)ий край$/i, "$1ом крае"],
    [/^(.+)ой край$/i, "$1ом крае"],
    [/^(.+)ий автономный округ(.*)$/i, "$1ом автономном округе$2"],
    [/^(.+)ая$/i, "$1ой"],
    // «Чувашия» → «Чувашии», но «Адыгея» → «Адыгее»: у -ия своё окончание.
    [/^(.+)ия$/i, "$1ии"],
    [/^(.+)я$/i, "$1е"],
    [/^(.+)ань$/i, "$1ани"],
    [/^(.+)а$/i, "$1е"],
  ];
  for (const [from, to] of rules) {
    if (from.test(name)) return name.replace(from, to);
  }
  // «Крым», «Севастополь», «Санкт-Петербург», «Дагестан» — мужской род.
  if (/(ь|й)$/i.test(name)) return name.replace(/(ь|й)$/i, "е");
  return `${name}е`;
};

/** Предлог «в» / «во»: «во Владимирской», но «в Курской». */
const prep = (name) => (/^вл/i.test(name) ? "во" : "в");

const page = ({ name, slug, districtNames }) => {
  const where = `${prep(name)} ${inflect(name)}`;
  const title = `Тревога и БПЛА ${where} — карта обстановки сейчас`;
  const description =
    `Воздушная обстановка ${where}: тревоги, фиксации БПЛА, работа ПВО и отбои ` +
    `по районам. Обновляется в реальном времени по открытым источникам.`;
  const url = `${SITE}/region/${slug}/`;

  const list = districtNames.length
    ? `<h2>Районы и округа</h2>
    <p>На карте видно обстановку по каждому из них отдельно:</p>
    <ul>${districtNames.map((d) => `<li>${escape(d)}</li>`).join("")}</ul>`
    : "";

  return `<!doctype html>
<html lang="ru">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>${escape(title)} · Тихое небо</title>
    <meta name="description" content="${escape(description)}" />
    <link rel="canonical" href="${url}" />
    <meta property="og:type" content="website" />
    <meta property="og:site_name" content="Тихое небо" />
    <meta property="og:locale" content="ru_RU" />
    <meta property="og:url" content="${url}" />
    <meta property="og:title" content="${escape(title)}" />
    <meta property="og:description" content="${escape(description)}" />
    <meta property="og:image" content="${SITE}/preview.png" />
    <meta name="theme-color" content="#0e1211" />
    <style>
      body { margin:0; background:#0b0f0e; color:#e6ebe6;
             font:16px/1.6 Inter, system-ui, -apple-system, sans-serif; }
      main { max-width:760px; margin:0 auto; padding:48px 20px 80px; }
      h1 { font-size:29px; line-height:1.25; margin:0 0 14px; }
      h2 { font-size:19px; margin:34px 0 10px; color:#eef2ec; }
      p { color:#aab4ad; }
      a.map { display:inline-block; margin:22px 0 6px; padding:13px 22px;
              background:#e93e4e; color:#fff; text-decoration:none;
              border-radius:10px; font-weight:600; }
      ul { columns:2; column-gap:28px; padding-left:20px; color:#aab4ad; }
      li { margin:3px 0; break-inside:avoid; }
      footer { margin-top:44px; padding-top:18px; font-size:13px; color:#7d8a83;
               border-top:1px solid rgba(255,255,255,.08); }
      footer a { color:#9fd4b0; }
      @media (max-width:560px) { ul { columns:1; } }
    </style>
  </head>
  <body>
    <main>
      <h1>${escape(title)}</h1>
      <p>${escape(description)}</p>

      <!-- Название на кнопке в именительном и через тире: родительный
           («карту Краснодарского края») требовал бы третьего набора
           правил ради одной строки, а предложный тут звучит неграмотно. -->
      <a class="map" href="/?region=${encodeURIComponent(slug)}">Открыть карту — ${escape(name)}</a>

      <h2>Что показывает карта</h2>
      <p>
        Тревоги и предупреждения об опасности, фиксации бортов, работа ПВО,
        взрывы и отбои — так, как о них сообщили открытые Telegram-каналы.
        У каждого события видно, сколько независимых источников его
        подтвердили, и можно открыть первоисточник.
      </p>

      ${list}

      <footer>
        Неофициальная карта: составлена по публичным сообщениям, может
        опаздывать и ошибаться. Не принимайте по ней решения о личной
        безопасности — следуйте указаниям экстренных служб.
        <br /><a href="/">Вся карта обстановки по России</a>
      </footer>
    </main>
  </body>
</html>
`;
};

const urls = [`${SITE}/`];
let made = 0;

for (const feature of regions.features) {
  const p = feature.properties || {};
  if (!p.name || !p.zone) continue;
  const slug = slugOf(p.zone);
  const names = (byRegion.get(p.id) || []).sort((a, b) => a.localeCompare(b, "ru"));

  const dir = join(outRoot, "region", slug);
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, "index.html"), page({ name: p.name, slug, districtNames: names }));
  urls.push(`${SITE}/region/${slug}/`);
  made += 1;
}

const sitemap = `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
${urls
  .map(
    (u) =>
      `  <url><loc>${u}</loc><changefreq>${u.endsWith("com/") ? "hourly" : "daily"}</changefreq>` +
      `<priority>${u.endsWith("com/") ? "1.0" : "0.7"}</priority></url>`
  )
  .join("\n")}
</urlset>
`;
writeFileSync(join(outRoot, "sitemap.xml"), sitemap);

console.log(`SEO: страниц регионов ${made}, в sitemap ${urls.length} адресов`);
