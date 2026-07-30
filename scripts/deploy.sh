#!/bin/zsh
# Выкатить проект на боевой сервер tihoenebo.com.
#
#     ./scripts/deploy.sh          # код + фронт + база
#     ./scripts/deploy.sh --code   # только код и фронт, база не трогается
#     ./scripts/deploy.sh --db     # только база (то же, что sync-db.sh)
#
# Что происходит:
#   1. Проверка (pytest + vitest + tsc) — на прод не уезжает сломанное.
#   2. Фронт собирается ЗДЕСЬ: на сервере node 18, а нашему Vite нужен 20+.
#      Готовый dist уезжает rsync-ом.
#   3. Код на сервере обновляется git pull из публичного репозитория —
#      значит перед выкаткой изменения должны быть запушены.
#   4. API перезапускается systemd-юнитом tihoenebo-api.
#
# Чего скрипт НЕ делает намеренно: не трогает Apache и чужие сайты
# (ton4.pro, tonsuite.org живут на той же машине) и не запускает на
# сервере сбор сообщений — сессия Telegram одна, она дома.
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

if [[ "$MODE" == "--db" ]]; then
  exec "$ROOT/scripts/sync-db.sh"
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
ssh "$SERVER" "cd $REMOTE_DIR && git pull --ff-only 2>&1 | tail -2"
rsync -az --delete -e ssh dist/ "$SERVER:$REMOTE_DIR/dist/"
ssh "$SERVER" "chmod -R o+rX $REMOTE_DIR/dist && systemctl restart tihoenebo-api"

if [[ "$MODE" != "--code" ]]; then
  echo "=== база"
  "$ROOT/scripts/sync-db.sh"
fi

echo "=== 5/5 проверка боевого"
sleep 4
# Пути в кавычках и с noglob: zsh иначе раскрывает «/» как шаблон и
# подставляет вместо него содержимое корня.
for path in "" "/api/v1/state" "/robots.txt"; do
  printf "  %-16s " "${path:-/}"
  "$CURL" -s -o /dev/null -w "%{http_code}\n" --max-time 20 "$SITE$path"
done
echo "готово: $SITE"
