#!/bin/zsh
# Проверка перед пушем: питон-тесты, веб-тесты, типы.
#
#     ./scripts/check.sh
#
# CI у проекта нет намеренно — пуш руками, проверка руками, одним вызовом.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

failed=0

echo "--- pytest"
ingest/.venv/bin/python -m pytest tests -q || failed=1

echo "--- vitest"
npx vitest run 2>&1 | tail -4 || failed=1
[[ ${pipestatus[1]} -ne 0 ]] && failed=1

echo "--- tsc"
npx tsc -b || failed=1

if [[ $failed -ne 0 ]]; then
  echo "\nПРОВЕРКА НЕ ПРОШЛА — пушить рано."
  exit 1
fi
echo "\nвсё зелёное: можно пушить"
