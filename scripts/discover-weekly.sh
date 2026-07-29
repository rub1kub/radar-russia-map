#!/bin/zsh
# Еженедельный поиск новых каналов-источников.
#
# Сессия Telegram одна на всё, а два клиента на одном файле сессии ломают
# друг друга. Поэтому на время прогона сборщик останавливается через
# launchd (bootout, не stop: KeepAlive поднял бы его обратно) и поднимается
# после — даже если discover упал.
#
# Результат — ingest/data/candidates.json; решение о включении за человеком.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DOMAIN="gui/$(id -u)"
PLIST="$HOME/Library/LaunchAgents/com.radar.poll.plist"

echo "--- discover $(date '+%F %T'): останавливаю сборщик"
launchctl bootout "$DOMAIN/com.radar.poll" 2>/dev/null || true
sleep 3

"$ROOT/ingest/.venv/bin/python" "$ROOT/ingest/discover.py" || true

sleep 2
if [[ -f "$PLIST" ]]; then
  launchctl bootstrap "$DOMAIN" "$PLIST" 2>/dev/null || true
fi
echo "--- discover: сборщик поднят, кандидаты в ingest/data/candidates.json"
