#!/bin/zsh
# Локальная разработка: API и веб-сервер на домашней машине.
#
#     ./scripts/install-launchd.sh            # установить и запустить
#     ./scripts/install-launchd.sh uninstall  # выгрузить и удалить
#
# ВАЖНО, ЧТО ЭТОТ СКРИПТ БОЛЬШЕ НЕ ДЕЛАЕТ.
#
# Сбор сообщений, разбор, чистка корпуса и поиск новых каналов переехали на
# боевой сервер (systemd-юниты tihoenebo-*). Дома их запускать нельзя:
# сессия Telegram одна на аккаунт, и второй клиент ломает первый — сбор на
# сервере встанет. По той же причине убрана и синхронизация базы: теперь
# данные рождаются на сервере, и заливка домашней базы поверх серверной
# затёрла бы всё, что тот успел собрать.
#
# Дома остаётся ровно то, что нужно для разработки: API на 8000 и vite.
# Оба читают локальную копию базы и на боевые данные не влияют.
#
# Свежую базу с сервера для отладки: ./scripts/pull-db.sh

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$ROOT/ingest/.venv/bin/python"
NODE_DIR="$(dirname "$(command -v node)")"
AGENTS_DIR="$HOME/Library/LaunchAgents"
LOG_DIR="$ROOT/ingest/data/logs"
DOMAIN="gui/$(id -u)"

# Список полный намеренно: прежние агенты должны выгружаться при каждом
# запуске, даже когда мы их больше не создаём, — иначе установленный
# когда-то сборщик так и остался бы работать и мешал серверному.
LABELS=(com.radar.poll com.radar.pipeline com.radar.api com.radar.web
        com.radar.retention com.radar.discover com.radar.sync)

unload_all() {
  for label in "${LABELS[@]}"; do
    launchctl bootout "$DOMAIN/$label" 2>/dev/null || true
    rm -f "$AGENTS_DIR/$label.plist"
  done
}

kill_legacy() {
  # Старые nohup-процессы. pkill по полному пути — чтобы не задеть чужое.
  pkill -f "ingest/poll.py" 2>/dev/null || true
  pkill -f "pipeline.incremental" 2>/dev/null || true
  pkill -f "uvicorn api.server:app" 2>/dev/null || true
  pkill -f "$ROOT/node_modules/.bin/vite" 2>/dev/null || true
  sleep 2
}

if [[ "${1:-}" == "uninstall" ]]; then
  unload_all
  echo "агенты выгружены и удалены"
  exit 0
fi

[[ -x "$PY" ]] || { echo "нет $PY — сначала ingest/README.md"; exit 1; }
mkdir -p "$AGENTS_DIR" "$LOG_DIR"

# ProgramArguments агентов. KeepAlive перезапускает упавшее; ThrottleInterval
# не даёт крутиться в цикле, если процесс падает сразу.
write_agent() { # label, keepalive|daily, argv...
  local label="$1" mode="$2"; shift 2
  local args=""
  for arg in "$@"; do
    args+="        <string>$arg</string>\n"
  done
  local schedule=""
  if [[ "$mode" == "daily" ]]; then
    schedule="    <key>StartCalendarInterval</key>
    <dict><key>Hour</key><integer>5</integer><key>Minute</key><integer>10</integer></dict>"
  elif [[ "$mode" == "weekly" ]]; then
    # Понедельник, раннее утро: эфир тихий, остановка сборщика незаметна.
    schedule="    <key>StartCalendarInterval</key>
    <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>5</integer><key>Minute</key><integer>40</integer></dict>"
  elif [[ "$mode" == "every2min" ]]; then
    # Свежая база уезжает на боевой сервер. Две минуты — компромисс:
    # тревога на сайте отстаёт ненадолго, а дельта-rsync стоит копейки.
    schedule="    <key>StartInterval</key><integer>120</integer>"
  elif [[ "$mode" == "once" ]]; then
    # Одна попытка при входе: macOS может не пустить node к Documents
    # (TCC), и KeepAlive крутил бы вечный цикл падений. Карта при этом
    # всё равно доступна: собранную статику раздаёт API на 8000.
    schedule="    <key>RunAtLoad</key><true/>"
  else
    schedule="    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>ThrottleInterval</key><integer>15</integer>"
  fi
  cat > "$AGENTS_DIR/$label.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>$label</string>
    <key>WorkingDirectory</key><string>$ROOT</string>
    <key>EnvironmentVariables</key>
    <dict><key>PATH</key><string>$NODE_DIR:/usr/bin:/bin:/usr/sbin:/sbin</string></dict>
    <key>ProgramArguments</key>
    <array>
$(printf "$args")    </array>
$schedule
    <key>StandardOutPath</key><string>$LOG_DIR/$label.log</string>
    <key>StandardErrorPath</key><string>$LOG_DIR/$label.log</string>
</dict>
</plist>
PLIST
}

# Сначала выгрузка прежних версий: unload_all заодно удаляет их plist,
# поэтому вызывается строго ДО записи новых.
unload_all >/dev/null 2>&1 || true
kill_legacy

# Только разработка. Сбор, разбор, ретеншн и discover живут на сервере —
# см. оговорку в шапке файла.
write_agent com.radar.api  keepalive "$PY" -m uvicorn api.server:app --host 127.0.0.1 --port 8000
write_agent com.radar.web  once      "$ROOT/node_modules/.bin/vite" --host 127.0.0.1

LABELS=(com.radar.api com.radar.web)

# Bootstrap с одним повтором: сразу после выгрузки прежней версии порт или
# сессия пару секунд ещё заняты, и первая попытка может упасть гонкой.
for label in "${LABELS[@]}"; do
  if ! launchctl bootstrap "$DOMAIN" "$AGENTS_DIR/$label.plist" 2>/dev/null; then
    sleep 3
    launchctl bootstrap "$DOMAIN" "$AGENTS_DIR/$label.plist" \
      || echo "НЕ ПОДНЯЛСЯ: $label (см. лог)"
  fi
done

sleep 3
echo "--- launchctl:"
launchctl list | grep com.radar || true
echo "--- логи: $LOG_DIR"
