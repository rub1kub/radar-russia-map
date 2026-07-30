#!/bin/zsh
# Забрать боевую базу на домашнюю машину — для отладки.
#
#     ./scripts/pull-db.sh
#
# Данные теперь рождаются на сервере: там сбор, там разбор. Дома база нужна
# только чтобы посмотреть на реальные события в отладчике или прогнать по
# ним новый разбор, поэтому направление ровно одно — с сервера сюда.
#
# Обратной команды нет намеренно: заливка домашней базы на сервер затёрла
# бы всё, что тот успел собрать.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SERVER="${RADAR_SERVER:-root@144.31.30.62}"
REMOTE_DIR="${RADAR_REMOTE_DIR:-/opt/tihoenebo}"
LOCAL="$ROOT/ingest/data/radar.db"

echo "--- снимок боевой базы"
# Через backup, а не копированием: сервер пишет в базу прямо сейчас.
ssh "$SERVER" "cd $REMOTE_DIR && .venv/bin/python -c \"
import sqlite3
src = sqlite3.connect('file:ingest/data/radar.db?mode=ro', uri=True)
dst = sqlite3.connect('/tmp/radar-pull.db')
src.backup(dst); dst.close(); src.close()
\""

echo "--- забираю"
rsync -z --progress -e ssh "$SERVER:/tmp/radar-pull.db" "$LOCAL"
ssh "$SERVER" "rm -f /tmp/radar-pull.db"

echo "--- готово"
"$ROOT/ingest/.venv/bin/python" -c "
import sqlite3
c = sqlite3.connect('$LOCAL')
n = c.execute('SELECT COUNT(*) FROM raw_messages').fetchone()[0]
e = c.execute('SELECT COUNT(*) FROM events').fetchone()[0]
print(f'сообщений {n}, событий {e}')
"
