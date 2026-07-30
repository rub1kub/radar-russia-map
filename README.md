# Radar Russia Map

Карта воздушной обстановки по публичным Telegram-лентам: сбор сообщений из
12 каналов, разбор текста, разрешение топонимов до населенного пункта, слияние
источников с расчетом достоверности, API и карта на React + OpenLayers.

> Статус: работает сквозной срез конвейера — от Telegram до карты.
> Целевая архитектура: [docs/TARGET_ARCHITECTURE.md](docs/TARGET_ARCHITECTURE.md).
> Состояние картографической части: [база знаний](docs/PROJECT_KNOWLEDGE_BASE.md).

## Запуск всего стека

Нужны три процесса. Порядок важен только для первого запуска.

```bash
# 1. Справочник зон: 89 регионов -> 2 327 районов -> 209 789 НП с иерархией.
#    Один раз после каждого prepare:data.
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

`npm run dev` перед каждым запуском заново выполняет `prepare:data`. Это занимает
время и перезаписывает `public/data/*.json`.

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
- В проекте пока нет backend, БД, API, тестов, CI/CD и production-конфигурации.


## Боевой сервер

Карта работает на **https://tihoenebo.com** (Нидерланды, Ubuntu 24.04).

Разделение обязанностей намеренное: **сбор сообщений живёт дома**, потому
что сессия Telegram одна и второй экземпляр сборщика её ломает; сервер
только отдаёт готовые данные. Свежая база уезжает на сервер каждые две
минуты launchd-агентом `com.radar.sync`.

| Что | Где |
|---|---|
| Код | `/opt/tihoenebo` (git pull из этого репозитория) |
| Статика | `/opt/tihoenebo/dist` (собирается локально: на сервере node 18, а Vite нужен 20+) |
| API | `systemd`-юнит `tihoenebo-api`, порт **8010** — 8000 занят чужим контейнером |
| Веб | Apache, `sites-available/tihoenebo.com*.conf`, SSL от Let's Encrypt |
| База | `/opt/tihoenebo/ingest/data/radar.db` |

На той же машине живут посторонние сайты (`ton4.pro`, `tonsuite.org`) и
docker-стек — их конфиги трогать нельзя, поэтому у нас отдельный vhost и
отдельный порт.

### Выкатить обновление

```bash
./scripts/deploy.sh          # проверки, сборка, код + фронт + база
./scripts/deploy.sh --code   # только код и фронт
./scripts/deploy.sh --db     # только свежая база
```

`deploy.sh` останавливается, если есть неотправленные коммиты: на сервер
код приезжает через `git pull`, поэтому сначала `git push`.
