# Radar Russia Map

[Тихое небо](https://tihoenebo.com) — карта воздушной обстановки по
отобранным публичным Telegram-лентам: сбор сообщений, разрешение топонимов
до населенного пункта, слияние источников с расчетом достоверности, API и
интерфейс на React + OpenLayers.

> Статус: работает сквозной срез конвейера — от Telegram до карты.
> Целевая архитектура: [docs/TARGET_ARCHITECTURE.md](docs/TARGET_ARCHITECTURE.md).
> Состояние картографической части: [база знаний](docs/PROJECT_KNOWLEDGE_BASE.md).

## Запуск всего стека

Нужны три процесса. Порядок важен только для первого запуска.

```bash
# 1. Справочник зон: 90 региональных зон -> 2 416 районов -> 209 789 НП.
#    Нужен при первом запуске или после изменения геометрии/иерархии.
ingest/.venv/bin/python -m pipeline.gazetteer

# 2. Разбор накопленных сообщений в события.
ingest/.venv/bin/python -m pipeline.rebuild

# 3. API конвейера.
ingest/.venv/bin/uvicorn api.server:app --port 8000

# 4. Непрерывный сбор из Telegram в raw_messages.
ingest/.venv/bin/python ingest/listen.py

# 5. Карта.
npx vite --host 127.0.0.1
```

Карта работает и без API — просто без слоя обстановки. Индикатор в панели
«Обстановка» показывает, поднят ли конвейер.

Парсер меняется постоянно, поэтому `rebuild.py` пересобирает события из
`raw_messages` целиком. Сырые сообщения при этом не трогаются.

## Конвейер

| Модуль | Ответственность |
|---|---|
| [`ingest/`](ingest/) | Telegram-сессия, каналы, выгрузка и live-прием |
| [`pipeline/gazetteer.py`](pipeline/gazetteer.py) | Справочник зон с иерархией из `public/data` |
| [`pipeline/parse.py`](pipeline/parse.py) | Текст -> сигнал, угроза, места, направление, счет |
| [`pipeline/geocode.py`](pipeline/geocode.py) | Топонимы -> зоны, снятие омонимии контекстом |
| [`pipeline/fuse.py`](pipeline/fuse.py) | Слияние источников, достоверность |
| [`pipeline/rebuild.py`](pipeline/rebuild.py) | Полный переразбор |
| [`api/server.py`](api/server.py) | `/state`, `/history`, `/analytics/*`, WebSocket |

## Быстрый запуск

Требования:

- Node.js 22;
- npm;
- системная утилита `unzip`;
- около 2 ГБ свободного места под рабочую копию;
- доступ в интернет для тайлов CARTO и Esri.

```bash
npm ci
npm run dev
```

Локальная карта: <http://127.0.0.1:5173/>

`npm run dev` вызывает `prepare:data`; неизменившиеся входы пропускаются по
штампу. При реальной пересборке имена и родители районов автоматически
проверяются по ОКТМО в одноразовой БД, живая база не затрагивается.

## Основные команды

```bash
npm run prepare:data   # собрать клиентские JSON из research/data_sources
npm run prepare:hydro  # пересобрать промежуточную сеть HydroRIVERS
npm run build          # prepare:data + TypeScript + production bundle
npm run preview        # открыть dist на http://127.0.0.1:4173/
```

Если менялись границы регионов или исходные архивы HydroRIVERS, полный порядок
пересборки такой:

```bash
npm run prepare:data
npm run prepare:hydro
npm run prepare:data
```

## Структура

- `src/` — React-интерфейс, OpenLayers-карта и стили.
- `scripts/` — ETL, фильтрация и компактирование геоданных.
- `research/data_sources/` — исходные открытые наборы.
- `research/radarmap_reference/` — архивные образцы RadarMap только для анализа.
- `public/data/` — генерируемые данные, которые загружает браузер.
- `docs/PROJECT_KNOWLEDGE_BASE.md` — каноническая документация проекта.

## Важные ограничения

- Не редактировать `public/data` вручную: следующий `dev` или `build` затрет правки.
- Не размещать ассеты в `public/icons`: ETL удаляет эту папку.
- RadarMap-ассеты и сохраненные GeoJSON не имеют подтвержденной публичной лицензии
  и не должны попадать в публичный продукт без разрешения.
- Текущие Natural Earth дороги, железные дороги и городские полигоны являются
  обзорными данными, а не полной уличной картой.
- Выкладка и проверки автоматизированы локальными скриптами, но внешнего CI нет.


## Боевой сервер

Карта работает на **https://tihoenebo.com** (Нидерланды, Ubuntu 24.04).
Там же живёт весь конвейер: сбор, разбор, API и чистка корпуса.

| Что | Где |
|---|---|
| Сбор сообщений | systemd `tihoenebo-poll` (246 источников, цикл 45 с) |
| Разбор в события | systemd `tihoenebo-pipeline` (цикл 20 с) |
| API | systemd `tihoenebo-api`, порт **8010** — 8000 занят чужим контейнером |
| Чистка корпуса | таймер `tihoenebo-retention.timer`, ежедневно в 05:10 |
| Веб | Apache, `sites-available/tihoenebo.com*.conf`, SSL от Let's Encrypt |
| Код и статика | `/opt/tihoenebo`, статика собирается локально (на сервере node 18, Vite нужен 20+) |

**Дома сбор запускать нельзя.** Сессия Telegram одна на аккаунт: второй
клиент ломает серверный, и сбор на боевом встанет. Локально работают
только API (порт 8000) и vite — для разработки, поверх копии базы.

На той же машине живут посторонние сайты (`ton4.pro`, `tonsuite.org`) и
docker-стек — их конфиги трогать нельзя, поэтому у нас отдельный vhost и
отдельный порт.

### Команды

```bash
./scripts/deploy.sh        # выкатить код и фронт на боевой
./scripts/pull-db.sh       # забрать боевую базу для отладки
./scripts/check.sh         # pytest + vitest + tsc перед пушем
./scripts/install-launchd.sh   # локальные агенты (только API и веб)
```

`deploy.sh` останавливается, если есть неотправленные коммиты: на сервер
код приезжает через `git pull`, поэтому сначала `git push`.
