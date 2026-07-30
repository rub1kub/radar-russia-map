"""Отправить свежую базу на боевой сервер.

    ingest/.venv/bin/python scripts/sync_db.py

Раньше это был zsh-скрипт, но launchd-агенту macOS не даёт /bin/zsh
читать файлы из ~/Documents (та же защита TCC, из-за которой там же не
поднимается vite). Питону из ingest/.venv доступ уже выдан — на нём
работает сбор, — поэтому синхронизация переехала сюда.

Сбор сообщений живёт дома: сессия Telegram одна, и второй экземпляр
сборщика её ломает (см. CLAUDE.md). Сервер только отдаёт готовые данные.

Три предосторожности:
  1. Снимок делается через sqlite3 backup, а не копированием файла:
     база в этот момент пишется, и сырая копия была бы порванной.
  2. На сервер файл едет во временное имя и только потом переносится
     поверх боевого — mv в пределах одной ФС атомарен, читающий API не
     увидит полузалитую базу.
  3. Перед передачей боевая база копируется в то же временное имя,
     чтобы rsync посчитал разницу и отправил только её: полная заливка
     гнала бы по сети больше сотни мегабайт каждые две минуты.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVER = os.environ.get("RADAR_SERVER", "root@144.31.30.62")
REMOTE_DIR = os.environ.get("RADAR_REMOTE_DIR", "/opt/tihoenebo")
REMOTE_DB = f"{REMOTE_DIR}/ingest/data/radar.db"
LOCAL_DB = ROOT / "ingest" / "data" / "radar.db"


def run(command: list[str]) -> None:
    subprocess.run(command, check=True, capture_output=True, text=True, timeout=600)


def main() -> int:
    if not LOCAL_DB.exists():
        print("базы нет:", LOCAL_DB)
        return 1

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as handle:
        snapshot = Path(handle.name)

    try:
        source = sqlite3.connect(f"file:{LOCAL_DB}?mode=ro", uri=True)
        target = sqlite3.connect(snapshot)
        source.backup(target)
        target.close()
        source.close()

        # Основа для дельты: копия боевой базы под именем, куда льём.
        run(["ssh", SERVER, f"cp -f {REMOTE_DB} {REMOTE_DB}.incoming 2>/dev/null || true"])
        run(["rsync", "-z", "--partial", "--inplace", "-e", "ssh",
             str(snapshot), f"{SERVER}:{REMOTE_DB}.incoming"])
        run(["ssh", SERVER, f"mv {REMOTE_DB}.incoming {REMOTE_DB}"])

        size = snapshot.stat().st_size / 1024 / 1024
        print(f"отправлено, снимок {size:.0f} МБ")
        return 0
    except subprocess.CalledProcessError as error:
        print("ошибка передачи:", error.stderr.strip()[:400])
        return 1
    finally:
        snapshot.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
