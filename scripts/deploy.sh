#!/bin/zsh
# Выкатить проект на боевой сервер tihoenebo.com.
#
#     ./scripts/deploy.sh          # проверки, сборка, код и фронт
#
# База не заменяется: сбор идёт на сервере, там она и живёт. Миграционный
# шаг может только синхронизировать канонические имена по стабильным ID.
#
# Что происходит:
#   1. Проверка (pytest + vitest + tsc) — на прод не уезжает сломанное.
#   2. Фронт собирается ЗДЕСЬ: на сервере node 18, а нашему Vite нужен 20+.
#      Готовый dist уезжает rsync-ом.
#   2а. Имена регионов, районов и НП синхронизируются в живой БД без смены
#      zone ID, затем
#      посадочные и дневные сводки собираются НА СЕРВЕРЕ. Заодно уходит
#      пинг IndexNow.
#   3. Код на сервере обновляется git pull из публичного репозитория —
#      значит перед выкаткой изменения должны быть запушены.
#   4. API перезапускается systemd-юнитом tihoenebo-api.
#
# Чего скрипт НЕ делает намеренно: не трогает Apache и чужие сайты
# (ton4.pro, tonsuite.org живут на той же машине) и не перезапускает сбор —
# tihoenebo-poll работает сам по себе, а лишний рестарт рвал бы соединение
# с Telegram на ровном месте.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
SERVER="${RADAR_SERVER:-root@144.31.30.62}"
REMOTE_DIR="${RADAR_REMOTE_DIR:-/opt/tihoenebo}"
SITE="${RADAR_SITE:-https://tihoenebo.com}"

MODE="${1:-all}"

# Полный путь к curl запоминается до сборки: npm перестраивает окружение
# под собой, и после него простое «curl» в этом скрипте не находилось.
CURL="$(command -v curl)"

# Сеть до сервера временами дремлет: выкатка дважды обрывалась на первом же
# ssh по таймауту, хотя со второй попытки проходила сразу. Каждый сетевой
# шаг повторяется до трёх раз с паузой — но только сетевой: упавшие тесты
# или сборку повторять бессмысленно.
retry() {
  local attempt
  for attempt in 1 2 3; do
    "$@" && return 0
    [[ $attempt == 3 ]] && return 1
    echo "  сеть не ответила (попытка $attempt из 3), жду 5 секунд…"
    sleep 5
  done
}

if [[ "$MODE" == "--db" ]]; then
  echo "База больше не заливается: сбор идёт на сервере, и домашняя копия"
  echo "затёрла бы собранное. Забрать боевую базу для отладки:"
  echo "  ./scripts/pull-db.sh"
  exit 1
fi

echo "=== 1/5 проверка"
./scripts/check.sh

echo "=== 2/5 незапушенные изменения?"
if [[ -n "$(git status --porcelain)" ]]; then
  echo "ВНИМАНИЕ: есть незакоммиченные правки — на сервер уедет только запушенное:"
  git status --short
fi
if [[ -n "$(git log origin/main..HEAD --oneline 2>/dev/null)" ]]; then
  echo "ОСТАНОВКА: есть коммиты, не отправленные в origin/main. Сначала git push."
  git log origin/main..HEAD --oneline
  exit 1
fi

echo "=== 3/5 сборка фронта"
VITE_API_BASE="$SITE" npm run build 2>&1 | tail -3

echo "=== 4/5 выкатка"
# Пайп к tail раньше глотал код ошибки: упавший по сети pull выглядел
# успехом, сервер молча оставался на старом коммите, а пересборка корпуса
# шла старым кодом. Теперь pull без пайпа, а следом жёсткая сверка: HEAD
# сервера обязан совпасть с тем, что мы только что запушили.
retry ssh "$SERVER" "cd $REMOTE_DIR && git pull --ff-only"
EXPECTED="$(git rev-parse origin/main)"
DEPLOYED=""
for attempt in 1 2 3; do
  if DEPLOYED="$(ssh "$SERVER" "cd $REMOTE_DIR && git rev-parse HEAD")"; then
    break
  fi
  [[ $attempt == 3 ]] && break
  echo "  HEAD сервера не прочитан (попытка $attempt из 3), жду 5 секунд…"
  sleep 5
done
if [[ -z "$DEPLOYED" ]]; then
  echo "ОСТАНОВКА: не удалось прочитать HEAD сервера после трёх попыток."
  exit 1
fi
if [[ "$DEPLOYED" != "$EXPECTED" ]]; then
  echo "ОСТАНОВКА: сервер на $DEPLOYED, ожидался $EXPECTED — pull не прошёл."
  exit 1
fi
# Страницы регионов, городов, ежедневные сводки и sitemap собираются на
# сервере: в них идёт сводка из базы, а база живёт только там. Из
# синхронизации они исключены, иначе
# --delete снёс бы их до того, как соберётся новая версия, и всё это время
# посадочные отдавали бы 404.
retry rsync -az --delete --exclude "region/" --exclude "city/" \
  --exclude "svodka/" --exclude "sitemap.xml" \
  --exclude "privacy/" --exclude "terms/" \
  -e ssh dist/ "$SERVER:$REMOTE_DIR/dist/"
retry ssh "$SERVER" "cd $REMOTE_DIR && PYTHONPATH=$REMOTE_DIR:$REMOTE_DIR/ingest \
  ./.venv/bin/python -m scripts.sync_place_names"
# Правовые страницы: содержимое от данных не зависит, поэтому не в
# почасовом таймере SEO, а здесь — один раз за выкатку.
retry ssh "$SERVER" "cd $REMOTE_DIR && PYTHONPATH=$REMOTE_DIR:$REMOTE_DIR/ingest \
  ./.venv/bin/python -m scripts.legal_pages"
retry ssh "$SERVER" "cd $REMOTE_DIR && PYTHONPATH=$REMOTE_DIR:$REMOTE_DIR/ingest \
  ./.venv/bin/python -m scripts.seo_pages --ping 2>&1 | tail -2"
retry ssh "$SERVER" "chmod -R o+rX $REMOTE_DIR/dist && systemctl restart tihoenebo-api"

echo "=== 5/5 проверка боевого"
sleep 4
# Пути в кавычках и с noglob: zsh иначе раскрывает «/» как шаблон и
# подставляет вместо него содержимое корня.
for path in "" "/api/v1/state" "/robots.txt" "/city/krasnodar/" \
  "/svodka/$(date +%F)/"; do
  printf "  %-16s " "${path:-/}"
  "$CURL" -s -o /dev/null -w "%{http_code}\n" --max-time 20 "$SITE$path"
done
echo "готово: $SITE"
