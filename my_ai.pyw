"""Запуск «Мой ИИ»: дважды щёлкните по этому файлу.

Весь код программы лежит в папке backend (Python) и frontend (то, что видно в окне).
"""

import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "backend"))

try:
    import server
    server.run()
except Exception:
    (ROOT / "data").mkdir(exist_ok=True)
    with open(ROOT / "data" / "log.txt", "a", encoding="utf-8") as f:
        f.write(traceback.format_exc() + "\n")
    raise
