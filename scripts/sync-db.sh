#!/bin/zsh
# Отправить свежую базу на боевой сервер.
#
#     ./scripts/sync-db.sh
#
# Сбор сообщений живёт дома: сессия Telegram одна, и второй экземпляр
# сборщика её ломает (см. CLAUDE.md). Поэтому конвейер работает на
# домашней машине, а сервер только отдаёт готовые данные — и получает их
# этой синхронизацией.
#
# Три предосторожности:
#   1. Снимок делается через sqlite3 backup, а не копированием файла:
#      база в этот момент пишется, и сырая копия была бы порванной.
#   2. На сервер файл едет во временное имя и только потом переносится
#      поверх боевого — mv в пределах одной ФС атомарен, читающий API
#      не увидит полузалитую базу.
#   3. API открывает соединение к SQLite на каждый запрос, поэтому
#      перезапускать его после подмены файла не нужно.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SERVER="${RADAR_SERVER:-root@144.31.30.62}"
REMOTE_DIR="${RADAR_REMOTE_DIR:-/opt/tihoenebo}"
SNAPSHOT="$(mktemp -t radar-snapshot).db"

cleanup() { rm -f "$SNAPSHOT"; }
trap cleanup EXIT

echo "--- снимок базы"
"$ROOT/ingest/.venv/bin/python" - "$SNAPSHOT" <<'PY'
import sqlite3, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
source = sqlite3.connect("file:ingest/data/radar.db?mode=ro", uri=True)
target = sqlite3.connect(sys.argv[1])
source.backup(target)
target.close()
source.close()
PY

SIZE=$(du -h "$SNAPSHOT" | cut -f1)
echo "--- отправка ($SIZE)"
# Копия боевой базы кладётся во временный файл ДО передачи: тогда rsync
# видит на приёмнике почти такой же файл и шлёт только различия. Без этого
# каждый прогон гнал бы по сети все сто с лишним мегабайт заново.
ssh "$SERVER" "cp -f $REMOTE_DIR/ingest/data/radar.db $REMOTE_DIR/ingest/data/radar.db.incoming 2>/dev/null || true"
rsync -z --partial --inplace -e ssh "$SNAPSHOT" "$SERVER:$REMOTE_DIR/ingest/data/radar.db.incoming"

echo "--- атомарная замена"
ssh "$SERVER" "mv $REMOTE_DIR/ingest/data/radar.db.incoming $REMOTE_DIR/ingest/data/radar.db"

echo "--- проверка"
curl -s --max-time 20 https://tihoenebo.com/api/v1/summary || echo "(API не ответил)"
echo
