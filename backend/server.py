"""Сервер «Мой ИИ»: показывает интерфейс из папки frontend, общается с Ollama
и хранит данные (чаты, настройки, ваши темы) в папке data.

Здесь только Python-код. Всё, что видно в окне (страница, стили, скрипт, темы),
лежит отдельно в папке frontend.
"""

import json
import mimetypes
import os
import re
import secrets
import socket
import subprocess
import threading
import time
import traceback
import urllib.error
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# ---------- Настройки (можно менять) ----------
DEFAULT_MODEL = "huihui_ai/huihui-moe-abliterated:latest"
OLLAMA = "http://127.0.0.1:11434"
PORT = 8765
NUM_CTX = 8192        # сколько текста модель помнит в одном чате (больше = больше памяти)
KEEP_ALIVE = "30m"    # сколько держать модель в памяти после ответа
# -----------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
FRONT = ROOT / "frontend"
DATA = ROOT / "data"
CHATS = DATA / "chats"
USER_THEMES = DATA / "themes"
SETTINGS_FILE = DATA / "settings.json"
PROFILE = DATA / "profile"
LOG = DATA / "log.txt"

TOKEN = secrets.token_hex(16)
STATE = {"start": time.time(), "last_ping": 0.0, "bye_at": None}
CHAT_ID = re.compile(r"^[a-z0-9]{6,32}$")
THEME_ID = re.compile(r"^[a-z0-9_-]{2,40}$")
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
THEME_KEYS = ["--bg", "--side", "--surface", "--raised", "--line", "--text",
              "--muted", "--bubble", "--accent", "--accent2", "--code"]
STATIC_TYPES = {".html", ".css", ".js", ".svg", ".png", ".ico", ".woff2"}


def log(text):
    try:
        DATA.mkdir(exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {text}\n")
    except Exception:
        pass


def load_themes():
    """Готовые темы (frontend/themes) и ваши (data/themes). Пропущенные цвета берутся из «Полночи»."""
    found = []
    for folder, custom in ((FRONT / "themes", False), (USER_THEMES, True)):
        if not folder.exists():
            continue
        for f in sorted(folder.glob("*.json")):
            try:
                d = json.loads(f.read_text(encoding="utf-8-sig"))
                vars_ = {k: v for k, v in d.get("vars", {}).items()
                         if k in THEME_KEYS and isinstance(v, str) and HEX.match(v)}
                found.append({"id": f.stem, "name": str(d.get("name", f.stem))[:40],
                              "order": d.get("order", 100), "custom": custom, "vars": vars_})
            except Exception as e:
                log(f"тема {f.name} пропущена: {e}")
    base = next((t["vars"] for t in found if t["id"] == "midnight"), found[0]["vars"] if found else {})
    for t in found:
        t["vars"] = {**base, **t["vars"]}
        t["vars"] = {k: t["vars"][k] for k in THEME_KEYS if k in t["vars"]}
    found.sort(key=lambda t: (t["custom"], t["order"], t["name"]))
    return found


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, *args):
        pass

    # --- общие помощники ---
    def host_ok(self):
        return self.headers.get("Host", "") in (f"127.0.0.1:{PORT}", f"localhost:{PORT}")

    def authorized(self, query_token=None):
        token = query_token or self.headers.get("X-App-Token", "")
        return self.host_ok() and secrets.compare_digest(token, TOKEN)

    def send_data(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False)
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > 30_000_000:
            raise ValueError("слишком большой запрос")
        return json.loads(self.rfile.read(length) or b"{}")

    # --- GET: страницы и данные ---
    def do_GET(self):
        path = self.path.partition("?")[0]
        if not path.startswith("/api/"):
            return self.static(path)
        if not self.authorized():
            return self.send_data(403, {"error": "forbidden"})
        if path == "/api/models":
            return self.models()
        if path == "/api/chats":
            return self.list_chats()
        if path == "/api/themes":
            return self.send_data(200, load_themes())
        if path == "/api/settings":
            try:
                return self.send_data(200, SETTINGS_FILE.read_text(encoding="utf-8"))
            except FileNotFoundError:
                return self.send_data(200, {})
        m = re.fullmatch(r"/api/chats/([a-z0-9]+)", path)
        if m and CHAT_ID.match(m.group(1)):
            f = CHATS / f"{m.group(1)}.json"
            if f.exists():
                return self.send_data(200, f.read_text(encoding="utf-8"))
        self.send_data(404, {"error": "not found"})

    def static(self, path):
        if not self.host_ok():
            return self.send_data(403, {"error": "forbidden"})
        rel = "index.html" if path in ("", "/") else path.lstrip("/")
        f = (FRONT / rel).resolve()
        if FRONT.resolve() not in f.parents or not f.is_file() or f.suffix.lower() not in STATIC_TYPES:
            return self.send_data(404, {"error": "not found"})
        data = f.read_bytes()
        if f.name == "index.html":
            data = (data.decode("utf-8").replace("__TOKEN__", TOKEN)
                    .replace("__MODEL__", DEFAULT_MODEL)).encode("utf-8")
        ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
        if f.suffix.lower() in (".html", ".css", ".js", ".svg"):
            ctype += "; charset=utf-8"
        self.send_data(200, data, ctype)

    # --- PUT / DELETE ---
    def do_PUT(self):
        if not self.authorized():
            return self.send_data(403, {"error": "forbidden"})
        try:
            data = self.read_json()
            if self.path == "/api/settings":
                DATA.mkdir(exist_ok=True)
                return self.write_json(SETTINGS_FILE, data)
            m = re.fullmatch(r"/api/chats/([a-z0-9]+)", self.path)
            if m and CHAT_ID.match(m.group(1)):
                data["id"] = m.group(1)
                CHATS.mkdir(parents=True, exist_ok=True)
                return self.write_json(CHATS / f"{m.group(1)}.json", data)
            m = re.fullmatch(r"/api/themes/([a-z0-9_-]+)", self.path)
            if m and THEME_ID.match(m.group(1)):
                theme = {"name": str(data.get("name", "Моя тема"))[:40], "order": 100,
                         "vars": {k: v for k, v in data.get("vars", {}).items()
                                  if k in THEME_KEYS and isinstance(v, str) and HEX.match(v)}}
                USER_THEMES.mkdir(parents=True, exist_ok=True)
                return self.write_json(USER_THEMES / f"{m.group(1)}.json", theme, indent=2)
        except Exception as e:
            log(f"save error: {e}")
            return self.send_data(500, {"error": str(e)})
        self.send_data(404, {"error": "not found"})

    def write_json(self, target, data, indent=None):
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=indent), encoding="utf-8")
        os.replace(tmp, target)
        self.send_data(200, {"ok": True})

    def do_DELETE(self):
        if not self.authorized():
            return self.send_data(403, {"error": "forbidden"})
        m = re.fullmatch(r"/api/chats/([a-z0-9]+)", self.path)
        target = CHATS / f"{m.group(1)}.json" if m and CHAT_ID.match(m.group(1)) else None
        m = re.fullmatch(r"/api/themes/([a-z0-9_-]+)", self.path)
        if m and THEME_ID.match(m.group(1)):
            target = USER_THEMES / f"{m.group(1)}.json"  # удалять можно только свои темы
        if target is None:
            return self.send_data(404, {"error": "not found"})
        try:
            target.unlink()
        except FileNotFoundError:
            pass
        self.send_data(200, {"ok": True})

    # --- POST ---
    def do_POST(self):
        path, _, query = self.path.partition("?")
        if path == "/api/bye":  # окно закрывается; sendBeacon не умеет заголовки, токен в адресе
            if not self.authorized(query.replace("t=", "", 1)):
                return self.send_data(403, {"error": "forbidden"})
            STATE["bye_at"] = time.time() + 4
            return self.send_data(200, {"ok": True})
        if not self.authorized():
            return self.send_data(403, {"error": "forbidden"})
        if path == "/api/ping":
            STATE["last_ping"] = time.time()
            STATE["bye_at"] = None
            return self.send_data(200, {"ok": True})
        if path == "/api/chat":
            return self.chat()
        self.send_data(404, {"error": "not found"})

    # --- работа с Ollama ---
    def models(self):
        try:
            with urllib.request.urlopen(OLLAMA + "/api/tags", timeout=5) as r:
                tags = json.load(r)
            names = [m.get("model") or m.get("name") for m in tags.get("models", [])]
            self.send_data(200, {"models": [n for n in names if n]})
        except Exception as e:
            log(f"models error: {e}")
            self.send_data(200, {"models": [], "offline": True})

    def list_chats(self):
        items = []
        if CHATS.exists():
            for f in CHATS.glob("*.json"):
                try:
                    d = json.loads(f.read_text(encoding="utf-8"))
                    items.append({"id": f.stem, "title": d.get("title", "Чат"),
                                  "updated": d.get("updated", 0)})
                except Exception:
                    continue
        items.sort(key=lambda x: x["updated"], reverse=True)
        self.send_data(200, items)

    def chat(self):
        try:
            req = self.read_json()
            messages = req["messages"]
            if req.get("system"):
                messages = [{"role": "system", "content": req["system"]}] + messages
            payload = {"model": req["model"], "messages": messages, "stream": True,
                       "keep_alive": KEEP_ALIVE, "options": {"num_ctx": NUM_CTX}}
        except Exception as e:
            return self.send_data(400, {"error": f"неверный запрос: {e}"})

        upstream = None
        for think in (True, False):  # сначала просим «размышления», если модель не умеет - без них
            body = dict(payload)
            if think:
                body["think"] = True
            request = urllib.request.Request(
                OLLAMA + "/api/chat", json.dumps(body).encode("utf-8"),
                {"Content-Type": "application/json"})
            try:
                upstream = urllib.request.urlopen(request, timeout=900)
                break
            except urllib.error.HTTPError as e:
                text = e.read().decode("utf-8", "replace")
                if think and "think" in text.lower():
                    continue
                log(f"ollama http error: {text}")
                return self.send_data(502, {"error": text})
            except Exception as e:
                log(f"ollama unreachable: {e}")
                return self.send_data(502, {
                    "error": "Ollama не отвечает. Откройте Ollama из меню «Пуск» и повторите."})

        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            for line in upstream:
                self.wfile.write(line)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass  # окно закрыли или нажали «Остановить»
        finally:
            upstream.close()


class Server(ThreadingHTTPServer):
    daemon_threads = True


def port_busy():
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", PORT)) == 0


def open_window(url):
    if os.environ.get("MY_AI_NO_WINDOW"):
        return
    pf, pf86 = os.environ.get("PROGRAMFILES", ""), os.environ.get("PROGRAMFILES(X86)", "")
    local = os.environ.get("LOCALAPPDATA", "")
    candidates = [
        pf86 + r"\Microsoft\Edge\Application\msedge.exe",
        pf + r"\Microsoft\Edge\Application\msedge.exe",
        pf + r"\Google\Chrome\Application\chrome.exe",
        pf86 + r"\Google\Chrome\Application\chrome.exe",
        local + r"\Google\Chrome\Application\chrome.exe",
    ]
    for exe in candidates:
        if exe and os.path.exists(exe):
            subprocess.Popen([exe, f"--app={url}", "--window-size=1180,780",
                              "--no-first-run", "--no-default-browser-check",
                              f"--user-data-dir={PROFILE}"])
            return
    webbrowser.open(url)


def watchdog():
    while True:
        time.sleep(1)
        now = time.time()
        if STATE["bye_at"] and now > STATE["bye_at"]:
            os._exit(0)  # окно закрыто
        if STATE["last_ping"] == 0 and now - STATE["start"] > 180:
            os._exit(0)  # окно так и не открылось
        if STATE["last_ping"] and now - STATE["last_ping"] > 900:
            os._exit(0)  # страница давно молчит


def run():
    url = f"http://127.0.0.1:{PORT}/"
    if port_busy():  # программа уже запущена: просто показываем её окно
        open_window(url)
        return
    try:
        server = Server(("127.0.0.1", PORT), Handler)
    except OSError as e:
        log(f"не удалось занять порт {PORT}: {e}")
        raise
    threading.Thread(target=server.serve_forever, daemon=True).start()
    open_window(url)
    watchdog()


if __name__ == "__main__":
    try:
        run()
    except Exception:
        log(traceback.format_exc())
        raise
