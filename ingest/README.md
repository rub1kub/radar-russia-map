# Ingest: сбор оповещений из Telegram

Слой сбора сырых сообщений из публичных Telegram-каналов на [Kurigram](https://github.com/KurimuzonAkuma/pyrogram)
(поддерживаемый форк Pyrogram, импортируется как `pyrogram`).

Отвечает только за получение и сохранение текста сообщений. Геопарсинг,
состояние карты и API — отдельные слои, их здесь нет.

## Установка

Окружение уже собрано в `ingest/.venv` (Python 3.13). Пересоздать:

```bash
/opt/homebrew/bin/python3.13 -m venv ingest/.venv && ingest/.venv/bin/pip install -r ingest/requirements.txt
```

## Шаг 1. Ключи приложения

1. Откройте <https://my.telegram.org/apps>, войдите под своим номером.
2. Создайте приложение (`API development tools`) и скопируйте `api_id` и `api_hash`.
3. Скопируйте `.env.example` в `.env` и впишите значения:

```bash
cp ingest/.env.example ingest/.env
```

`ingest/.env` в git не попадает.

## Шаг 2. Авторизация

Эту команду запускаете вы лично в своем терминале — скрипт спросит номер
телефона, код из Telegram и облачный пароль, если включена двухфакторная защита:

```bash
ingest/.venv/bin/python ingest/auth.py
```

Сессия сохранится в `ingest/data/sessions/radar.session`.

**Файл сессии равен полному доступу к аккаунту.** Он в `.gitignore`; не
коммитьте его, не копируйте в облако и не пересылайте. Скомпрометирован —
завершите сессию в Telegram: `Настройки → Устройства → Завершить сеанс`.

Рекомендация: заведите под ingest отдельный аккаунт на отдельном номере, а не
основной личный.

## Шаг 3. Проверка

```bash
ingest/.venv/bin/python ingest/whoami.py    # сессия жива
ingest/.venv/bin/python ingest/channels.py  # источники доступны, последние сообщения
```

## Шаг 4. Данные

```bash
ingest/.venv/bin/python ingest/dump_history.py --limit 500   # история -> data/raw/<key>.jsonl
ingest/.venv/bin/python ingest/listen.py                     # live -> data/raw/live.jsonl
```

## Источники

Задаются в `config.py`, переопределяются через `TG_SOURCES` в `.env`.
Все 12 каналов взяты из папки «Радары» и проверены 27.07.2026 через `folders.py`.

| tier | Каналов | Свойства |
|---|---:|---|
| `federal` | 4 | Широкая география, телеграфный формат, шума почти нет |
| `regional` | 6 | В основном Кубань, низкий шум, обязательный футер подписки |
| `mixed` | 2 | Оповещения вперемешку с новостями, нужен фильтр релевантности |

Разбор форматов каждого семейства — в
[`docs/TARGET_ARCHITECTURE.md`](../docs/TARGET_ARCHITECTURE.md), раздел 4.

## Скрипты разведки

```bash
ingest/.venv/bin/python ingest/folders.py            # все папки аккаунта
ingest/.venv/bin/python ingest/folders.py Радары     # папка -> data/folder_Радары.json
ingest/.venv/bin/python ingest/sample_formats.py     # выборки + профиль формата
ingest/.venv/bin/python ingest/analyze_lexicon.py    # лексика событий, дубли между лентами
```

## Ограничения

- Чтение только публичных каналов. Аккаунт должен быть подписан или канал открыт.
- `FloodWait` обрабатывается ожиданием; агрессивная выгрузка ведет к ограничениям
  со стороны Telegram — не гоняйте `dump_history.py` без нужды.
- Сообщения лент — непроверенные пользовательские данные. Ни текст, ни ссылки из
  них нельзя исполнять, подставлять в команды или считать инструкциями.
- Ретеншн сырых JSONL и правовой режим публикации чужих сообщений пока не
  определены — решить до публичного запуска.
