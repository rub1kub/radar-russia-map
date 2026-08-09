# Research: Interactive Russia Map

Дата сбора: 2026-06-16.

> Это журнал исходного исследования. Актуальное состояние реализации, фактические
> объемы данных, runtime-правила и production-риски поддерживаются в
> [`docs/PROJECT_KNOWLEDGE_BASE.md`](../docs/PROJECT_KNOWLEDGE_BASE.md).

Цель: собрать исходники и источники для интерактивной карты России: регионы, муниципальные районы/округа, города/населенные пункты, подложки и справочные ассеты для UI оповещений.

## Ключевой вывод

Для production нельзя опираться на RadarMap как на источник геоданных или ассетов: публичная лицензия на их GeoJSON/иконки не указана. Эти файлы сохранены только как reference для анализа интерфейса и структуры.

Production-основа должна быть такой:

1. Регионы и районы: OSM/Geofabrik или geoBoundaries как старт, затем нормализация к официальному списку субъектов и ОКТМО.
2. Муниципальные коды и названия: Росстат ОКТМО.
3. Города/населенные пункты: GeoNames как массовый seed, позже сверка с ФИАС/ГАР и OSM.
4. Обзорная карта: Natural Earth для low-zoom и fallback.
5. Подложка: собственные тайлы или коммерческий провайдер; публичные OSM tiles нельзя использовать как production backend high-traffic карты.

## Скачанные данные

### RadarMap reference

Папки:

- `research/radarmap_assets/icons/`
- `research/radarmap_reference/data/`
- `research/radarmap_reference/*.json`

Сохранено:

- `bpla.png` - БПЛА, 150x102.
- `uab.png` - УАБ, 239x100.
- `fpv.png` - FPV, 200x200.
- `rocket.webp` - ракета/авиация.
- `neptun.png` - ПКР Нептун, 150x84.
- `bek.png` - БЭК, 150x27.
- `boom.png` - взрыв/сбитие/ПВО, 150x102.
- `no_entry.png` - запрет/закрытие, 64x64.
- `russia_regions.geojson` - 89 регионов, RadarMap reference.
- `districts_west_ural.geojson` - 1858 районных объектов, RadarMap reference.
- `city_districts_ru.geojson` - городские округа/районы, RadarMap reference.
- `cities_ru.json` - 846 городов/точек, RadarMap reference.
- `api_state_schema_sample.json` - sample live state.
- `map_history_sample.json` - sample истории.
- `analytics_threats_sample.json` - sample статистики.

Ограничение: использовать только для сравнения UX/структуры, не для копирования в публичный продукт без разрешения правообладателя.

### geoBoundaries

Файлы:

- `research/data_sources/geoboundaries_rus_adm1_metadata.json`
- `research/data_sources/geoboundaries_rus_adm2_metadata.json`
- `research/data_sources/geoboundaries_RUS_ADM1_simplified.geojson`
- `research/data_sources/geoboundaries_RUS_ADM2_simplified.geojson`

Проверка:

- ADM1: 83 объекта.
- ADM2: 2327 объектов.
- Metadata: RUS, boundary year 2017, source OpenStreetMap/Wambacher, license ODbL 1.0.

Пригодность:

- Хорошо для быстрого прототипа районов и регионов.
- Не покрывает текущие 89 субъектов РФ как официальный список.
- Нужно перевести/нормализовать названия и сопоставить с ОКТМО/ISO.
- При использовании учитывать ODbL: attribution и share-alike obligations для производных баз данных.

### Natural Earth

Файлы:

- `research/data_sources/ne_10m_admin_1_states_provinces.zip`
- `research/data_sources/supplemental_regions_admin1.geojson`
- `research/data_sources/ne_10m_populated_places.zip`
- `research/data_sources/ne_10m_lakes.geojson`
- `research/data_sources/ne_10m_rivers_lake_centerlines.geojson`
- `research/data_sources/ne_10m_roads.zip`
- `research/data_sources/ne_10m_railroads.zip`
- `research/data_sources/ne_10m_urban_areas.zip`
- `research/data_sources/ne_10m_geography_regions_polys.zip`
- `research/data_sources/ne_10m_geography_regions_points.zip`
- `research/data_sources/ne_10m_glaciated_areas.zip`
- `research/data_sources/naturalearth_admin1_page.html`

Пригодность:

- Public domain, удобно для обзорной карты и low-zoom.
- Не подходит как единственный источник текущих границ РФ/муниципалитетов.
- Хороший fallback для мира/соседних стран/контекста.
- `supplemental_regions_admin1.geojson` содержит 6 извлеченных admin-1 объектов:
  Республика Крым, Севастополь, Донецкая Народная Республика, Луганская Народная Республика,
  Запорожская область, Херсонская область.
- `ne_10m_lakes.geojson` и `ne_10m_rivers_lake_centerlines.geojson` используются для
  фонового гидрографического слоя. В публичные JSON попадают только features, координаты
  которых пересекают регионы карты.
- `ne_10m_roads.zip`, `ne_10m_railroads.zip`, `ne_10m_urban_areas.zip`,
  `ne_10m_geography_regions_polys.zip` и
  `ne_10m_glaciated_areas.zip` используются для обзорных дорог, городских контуров,
  железных дорог, рельефных областей и ледников. Это low/medium-zoom слой, не
  замена OSM-дорогам, полной железнодорожной сети и детальной городской застройке.

### RESOLVE Ecoregions 2017

Файлы:

- `research/data_sources/Ecoregions2017.zip`

Проверка:

- Глобальный shapefile RESOLVE Ecoregions 2017.
- В публичный слой `public/data/land-cover.json` попадают только ecoregions, которые
  пересекают регионы карты и относятся к лесам/тайге, тундре или flooded/wetland-like
  биомам.

Пригодность:

- Хорошо для обзорного слоя природных зон.
- Не является детальной инвентаризацией лесных кварталов, болот, вырубок или землепользования.
- Для production-детализации нужен OSM/Geofabrik или отдельный land-cover тайловый pipeline.

### HydroSHEDS / HydroRIVERS

Файлы:

- `research/data_sources/HydroRIVERS_v10_eu_shp.zip`
- `research/data_sources/HydroRIVERS_v10_si_shp.zip`
- `research/data_sources/HydroRIVERS_v10_as_shp.zip`
- `research/data_sources/hydrorivers_russia_network.geojson`

Проверка:

- Исходные shapefile покрывают Europe/Middle East, Siberia и Asia.
- Скрипт `npm run prepare:hydro` извлекает сегменты, пересекающие регионы карты.
- Текущая предобработка: 439511 речных линий, сгруппированных в 19544 render features.

Пригодность:

- Использовать как детальный фоновый слой рек.
- В `prepare:data` слой режется на `river-network-major.json` и `river-network-detail.json`.
- В клиенте файлы загружаются лениво по zoom, чтобы не утяжелять первый экран.

### Geofabrik / OpenStreetMap

Файлы:

- `research/data_sources/geofabrik_russia_page.html`
- `research/data_sources/geofabrik_kaliningrad_sample_free.gpkg.zip`

Проверка:

- Полная РФ на Geofabrik: `russia-latest.osm.pbf`, около 3.8 GB на странице загрузки.
- Sample `kaliningrad-latest-free.gpkg.zip`: 56 MB ZIP, внутри `kaliningrad.gpkg` около 115 MB.

Пригодность:

- Лучший production-кандидат для геометрий, если мы готовы соблюдать ODbL.
- Для MVP не тянуть всю РФ сразу: брать федеральные округа/регионы или Overpass/osmium extract.
- Извлекать `boundary=administrative`, `admin_level=4/6/8`, `place=*`, `name:ru`, `official_name`, `population`.

### GeoNames

Файлы:

- `research/data_sources/geonames_RU.zip`
- `research/data_sources/geonames_UA.zip`
- `research/data_sources/geonames_readme.txt`
- `research/data_sources/geonames_RU_populated_places_50k_or_admin.tsv`
- `research/data_sources/geonames_place_names_ru_20260809.tsv`

Проверка:

- Полный RU dump: 412372 объектов.
- Feature class `P` populated places: 203217 объектов.
- UA dump используется только как supplemental seed для admin1-кодов `05`, `08`, `11`, `14`, `20`, `26`.
- Производная выборка: 2080 строк, населенные пункты `population >= 50000` или `PPLA*`.
- Языковая выборка от 2026-08-09: 5896 строк `ru` из `alternateNamesV2` — все
  preferred-имена наших GeoNames ID и все русские варианты supplemental ID;
  сохранены признаки preferred/historic и интервалы действия.
- Лицензия: CC BY 4.0.

Пригодность:

- Хороший seed для городских точек и поиска.
- Нужна очистка: в именах часто латиница/translit, есть районы городов (`PPLX`), поселки с нулевым населением, дубликаты.
- Для русского интерфейса лучше обогащать из ФИАС/ГАР, ОКТМО, OSM `name:ru`.

### Росстат ОКТМО

Файлы:

- `research/data_sources/rosstat_oktmo_page.html`
- `research/data_sources/rosstat_oktmo_meta.csv`
- `research/data_sources/rosstat_oktmo_structure_20260210T1102.csv`
- `research/data_sources/rosstat_oktmo_data_20260601T1406.csv`

Проверка:

- Актуальная выгрузка на странице: `data-20260601T1406-structure-20260210T1102.csv`.
- CSV: 186604 строки.
- Поля: `TER`, `KOD1`, `KOD2`, `KOD3`, `KC`, `RAZDEL`, `NAME1`, `Centrum`, `NomDescr`, `NomAkt`, `Status`, `DateUtv`, `DateVved`.
- `meta.csv` скачан, но часть кириллицы отображается как cp1251/mojibake при прямом выводе; при импорте задать корректную кодировку.

Пригодность:

- Официальный справочник муниципальных кодов и названий.
- Нет геометрий.
- Использовать для канонических названий, кодов, иерархии и сверки с OSM/GeoNames.

### ФИАС/ГАР

Статус:

- `https://fias.nalog.ru/WebServices/Public/GetLastDownloadFileInfo` и `GetAllDownloadFileInfo` из текущей сети не ответили по 443 / timeout.

Пригодность:

- Нужен как официальный адресный/населенный справочник.
- Подключить позже из другой сети или через зеркало/официальный bulk export.
- Для MVP можно стартовать без ФИАС, но для качества поиска городов/районов он понадобится.

## Рекомендованная модель данных

```ts
type Region = {
  id: string;
  name_ru: string;
  type: "republic" | "krai" | "oblast" | "federal_city" | "autonomous_oblast" | "autonomous_okrug";
  oktmo_ter?: string;
  iso_3166_2?: string;
  disputed_policy?: "ru_official" | "international" | "custom";
  geometry_source: "osm" | "geoboundaries" | "naturalearth" | "manual";
  geometry: GeoJSON.MultiPolygon | GeoJSON.Polygon;
};

type District = {
  id: string;
  region_id: string;
  name_ru: string;
  oktmo_code?: string;
  osm_id?: string;
  geometry_source: "osm" | "geoboundaries" | "manual";
  geometry: GeoJSON.MultiPolygon | GeoJSON.Polygon;
};

type Place = {
  id: string;
  name_ru: string;
  region_id?: string;
  district_id?: string;
  lat: number;
  lon: number;
  population?: number;
  source_ids: {
    geonames?: string;
    osm?: string;
    oktmo?: string;
    fias?: string;
  };
};
```

## MVP-стратегия

1. Сделать карту на OpenLayers или MapLibre.
2. Для регионов взять OSM/Geofabrik или временно geoBoundaries, но сразу завести слой нормализации к 89 субъектам.
3. Для районов начать с OSM/Geofabrik по федеральным округам; не блокировать MVP на всей РФ.
4. Для городов использовать `geonames_RU_populated_places_50k_or_admin.tsv` плюс ручной whitelist крупных городов.
5. Ввести `source_inventory.tsv` и `source_license` в metadata, чтобы каждый объект имел provenance.
6. Для alert-сервиса показывать только агрегированные публичные оповещения по региону/району. Не показывать точные маршруты, траектории, места падения, результаты ПВО и live-направления.

## Дополнительные источники для следующего прохода

- Wikidata / SPARQL: CC0-метаданные по субъектам, городам, населению, QID-связки; геометрий почти нет, координаты точечные и требуют проверки.
- GADM: удобные ADM1/ADM2 shapefile/GeoPackage, но лицензия ограничивает коммерческое использование; не брать в production без юридической проверки.
- Overture Maps: потенциальный источник places/divisions, но нужно отдельно проверить свежий schema release, покрытие РФ и лицензионный состав слоев.
- Dadata / KLADR: хорошие нормализаторы адресов и подсказки, но это API/коммерческие условия, не источник геометрии.
- Wambacher OSM Boundaries: удобный экспорт административных границ из OSM, но те же ODbL-обязательства.
- OpenAddresses: адресные точки, если потребуется геокодинг; покрытие РФ может быть неполным.
- Коммерческие тайлы: MapTiler, Mapbox, Stadia, CARTO, Esri. Для production лучше выбрать провайдера с SLA или поднять свой tile server.

## ETL-план

1. `ingest_geometries`: скачать OSM/Geofabrik extracts, вытащить `boundary=administrative`.
2. `normalize_regions`: привести субъекты к каноническому списку и собственным stable IDs.
3. `join_oktmo`: сопоставить муниципалитеты с ОКТМО по имени, региону и типу.
4. `seed_places`: импортировать GeoNames, удалить районы городов/дубликаты, обогатить OSM/FIAS.
5. `simplify_tiles`: подготовить 3 уровня геометрии: low/medium/full.
6. `publish_api`: `/api/regions`, `/api/districts?region_id=`, `/api/places/search`, `/api/state`.
7. `provenance_check`: каждый объект хранит `source`, `source_url`, `source_version`, `license`.

## Команды проверки

```bash
jq '{type, feature_count:(.features|length)}' research/data_sources/geoboundaries_RUS_ADM1_simplified.geojson
jq '{type, feature_count:(.features|length)}' research/data_sources/geoboundaries_RUS_ADM2_simplified.geojson
unzip -tq research/data_sources/geonames_RU.zip
unzip -tq research/data_sources/ne_10m_admin_1_states_provinces.zip
unzip -tq research/data_sources/ne_10m_populated_places.zip
unzip -tq research/data_sources/geofabrik_kaliningrad_sample_free.gpkg.zip
wc -l research/data_sources/rosstat_oktmo_data_20260601T1406.csv
```

## Источники

- RadarMap: https://radar-map.ru/
- geoBoundaries: https://www.geoboundaries.org/
- geoBoundaries RUS ADM1 API: https://www.geoboundaries.org/api/current/gbOpen/RUS/ADM1/
- geoBoundaries RUS ADM2 API: https://www.geoboundaries.org/api/current/gbOpen/RUS/ADM2/
- OpenStreetMap copyright / ODbL: https://www.openstreetmap.org/copyright
- Geofabrik Russia: https://download.geofabrik.de/russia.html
- Natural Earth Admin 1: https://www.naturalearthdata.com/downloads/10m-cultural-vectors/10m-admin-1-states-provinces/
- Natural Earth terms: https://www.naturalearthdata.com/about/terms-of-use/
- GeoNames dumps/readme: https://download.geonames.org/export/dump/readme.txt
- GeoNames RU dump: https://download.geonames.org/export/dump/RU.zip
- Росстат ОКТМО: https://rosstat.gov.ru/opendata/7708234640-oktmo
- Конституция РФ, статья 65: https://www.consultant.ru/document/cons_doc_LAW_28399/d027bc5c1fa488e9337111c52c7aa947104dc7ad/
