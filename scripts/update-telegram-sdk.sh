#!/bin/zsh
# Обновить локальную копию SDK мини-приложений Telegram.
#
# Скрипт отдаётся со своего домена, потому что telegram.org в России
# недоступен: подключённый оттуда, он не загрузился бы у половины
# аудитории, и мини-приложение осталось бы обычной веб-страницей.
#
# Плата за это — копия стареет. Telegram добавляет методы новых версий
# Bot API (полный экран приехал в 8.0), поэтому раз в пару месяцев стоит
# обновляться и проверять карту в мессенджере.
#
#     ./scripts/update-telegram-sdk.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TARGET="$ROOT/public/vendor/telegram-web-app.js"
SOURCE="https://telegram.org/js/telegram-web-app.js"

TMP="$(mktemp)"
curl -fsSL --max-time 30 "$SOURCE" -o "$TMP"

# Пустой или обрезанный ответ хуже старой копии: подменять рабочий файл
# мусором нельзя.
if [[ ! -s "$TMP" ]] || (( $(wc -c < "$TMP") < 50000 )); then
  echo "ОСТАНОВКА: скачанный файл подозрительно мал, копия не тронута"
  rm -f "$TMP"
  exit 1
fi

if cmp -s "$TMP" "$TARGET"; then
  echo "SDK не изменился"
  rm -f "$TMP"
  exit 0
fi

mv "$TMP" "$TARGET"
echo "SDK обновлён: $(wc -c < "$TARGET") байт"
echo "Проверьте карту в Telegram перед выкаткой."
