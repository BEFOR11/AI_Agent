"""Мой ИИ: личное чат-приложение для локальной модели Ollama.

Запуск: двойной щелчок по my_ai.pyw (или: python my_ai.pyw).
Откроется отдельное окно без вкладок и адресной строки. Закроете окно, и программа сама завершится.

Ничего устанавливать не нужно: нужны только Python, запущенный Ollama и Edge или Chrome.
История чатов хранится в папке my_ai_chats рядом с этим файлом, наружу ничего не уходит.
Если что-то не работает, смотрите файл my_ai_log.txt рядом.
"""

import json
import os
import re
import secrets
import socket
import subprocess
import sys
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

BASE = Path(__file__).resolve().parent
CHATS = BASE / "my_ai_chats"
PROFILE = BASE / "my_ai_profile"
LOG = BASE / "my_ai_log.txt"
TOKEN = secrets.token_hex(16)
STATE = {"start": time.time(), "last_ping": 0.0, "bye_at": None}
ID_RE = re.compile(r"^[a-z0-9]{6,32}$")


def log(text):
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {text}\n")
    except Exception:
        pass


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

    # --- маршруты ---
    def do_GET(self):
        path, _, _query = self.path.partition("?")
        if path == "/":
            if not self.host_ok():
                return self.send_data(403, {"error": "forbidden"})
            page = PAGE.replace("__TOKEN__", TOKEN).replace("__MODEL__", DEFAULT_MODEL)
            return self.send_data(200, page, "text/html; charset=utf-8")
        if path == "/favicon.ico":
            return self.send_data(204, b"", "image/x-icon")
        if not self.authorized():
            return self.send_data(403, {"error": "forbidden"})
        if path == "/api/models":
            return self.models()
        if path == "/api/chats":
            return self.list_chats()
        m = re.fullmatch(r"/api/chats/([a-z0-9]+)", path)
        if m and ID_RE.match(m.group(1)):
            f = CHATS / f"{m.group(1)}.json"
            if f.exists():
                return self.send_data(200, f.read_text(encoding="utf-8"))
        return self.send_data(404, {"error": "not found"})

    def do_PUT(self):
        if not self.authorized():
            return self.send_data(403, {"error": "forbidden"})
        m = re.fullmatch(r"/api/chats/([a-z0-9]+)", self.path)
        if not (m and ID_RE.match(m.group(1))):
            return self.send_data(404, {"error": "not found"})
        try:
            data = self.read_json()
            data["id"] = m.group(1)
            CHATS.mkdir(exist_ok=True)
            tmp = CHATS / f"{m.group(1)}.tmp"
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, CHATS / f"{m.group(1)}.json")
            self.send_data(200, {"ok": True})
        except Exception as e:
            log(f"save error: {e}")
            self.send_data(500, {"error": str(e)})

    def do_DELETE(self):
        if not self.authorized():
            return self.send_data(403, {"error": "forbidden"})
        m = re.fullmatch(r"/api/chats/([a-z0-9]+)", self.path)
        if m and ID_RE.match(m.group(1)):
            try:
                (CHATS / f"{m.group(1)}.json").unlink()
            except FileNotFoundError:
                pass
            return self.send_data(200, {"ok": True})
        self.send_data(404, {"error": "not found"})

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


def main():
    url = f"http://127.0.0.1:{PORT}/"
    if port_busy():  # программа уже запущена: просто показываем её окно
        open_window(url)
        return
    server = Server(("127.0.0.1", PORT), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    open_window(url)
    watchdog()


PAGE = r"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Мой ИИ</title>
<style>
:root{
  --bg:#0e1014; --side:#12151b; --surface:#171b23; --raised:#1e232d; --line:#262c38;
  --text:#e8eaf0; --muted:#8a93a6; --faint:#5d6578; --bubble:#222838;
  --accent:#8b9bff; --accent2:#c58bff; --danger:#ff7a8a; --code:#0b0d11;
  --shadow:0 10px 40px rgba(0,0,0,.35);
  --sans:"Segoe UI Variable Text","Segoe UI",Inter,system-ui,sans-serif;
  --mono:"Cascadia Code","Cascadia Mono",Consolas,monospace;
}
@media (prefers-color-scheme: light){
  :root{
    --bg:#f6f7fa; --side:#eceef3; --surface:#ffffff; --raised:#ffffff; --line:#dfe2ea;
    --text:#1b1f29; --muted:#667085; --faint:#98a2b3; --bubble:#e6e9f4;
    --accent:#5b6cf0; --accent2:#a55bf0; --danger:#d6455a; --code:#f1f3f8;
    --shadow:0 10px 30px rgba(30,40,80,.12);
  }
}
*{box-sizing:border-box;margin:0}
html,body{height:100%}
body{background:var(--bg);color:var(--text);font:15px/1.6 var(--sans);overflow:hidden}
button,select,textarea{font:inherit;color:inherit}
button{cursor:pointer;border:0;background:none}
::selection{background:color-mix(in srgb,var(--accent) 40%,transparent)}
*{scrollbar-width:thin;scrollbar-color:var(--line) transparent}

.app{display:grid;grid-template-columns:268px 1fr;grid-template-rows:minmax(0,1fr);height:100%;transition:grid-template-columns .25s ease}
.app.collapsed{grid-template-columns:0 1fr}
.side{background:var(--side);border-right:1px solid var(--line);display:flex;flex-direction:column;overflow:hidden;min-width:0}
.brand{display:flex;align-items:center;gap:10px;padding:18px 18px 12px;font-weight:600;letter-spacing:.2px;white-space:nowrap}
.logo{width:28px;height:28px;border-radius:9px;display:grid;place-items:center;color:#fff;font-size:15px;
  background:linear-gradient(135deg,var(--accent),var(--accent2));box-shadow:0 4px 16px color-mix(in srgb,var(--accent) 45%,transparent)}
.new{margin:6px 12px 10px;padding:10px 14px;border-radius:12px;background:var(--surface);border:1px solid var(--line);
  display:flex;align-items:center;gap:8px;color:var(--text);transition:.15s;white-space:nowrap}
.new:hover{border-color:var(--accent);transform:translateY(-1px)}
.list{flex:1;overflow-y:auto;padding:4px 8px 12px}
.item{display:flex;align-items:center;gap:6px;padding:9px 10px;border-radius:10px;color:var(--muted);cursor:pointer;
  white-space:nowrap;transition:background .15s,color .15s}
.item span{flex:1;overflow:hidden;text-overflow:ellipsis}
.item:hover{background:color-mix(in srgb,var(--text) 6%,transparent);color:var(--text)}
.item.active{background:color-mix(in srgb,var(--accent) 16%,transparent);color:var(--text)}
.item .del{opacity:0;color:var(--faint);padding:0 4px;font-size:16px;line-height:1}
.item:hover .del{opacity:1}.item .del:hover{color:var(--danger)}
.side-foot{padding:12px 18px;color:var(--faint);font-size:12px;border-top:1px solid var(--line);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}

.main{display:flex;flex-direction:column;min-width:0;min-height:0;height:100%;position:relative;overflow:hidden}
.top{display:flex;align-items:center;gap:8px;padding:12px 16px}
.icon{width:36px;height:36px;border-radius:10px;display:grid;place-items:center;color:var(--muted);transition:.15s;font-size:17px}
.icon:hover{background:color-mix(in srgb,var(--text) 8%,transparent);color:var(--text)}
.model{appearance:none;background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:7px 30px 7px 12px;
  max-width:min(420px,52vw);text-overflow:ellipsis;cursor:pointer;color:var(--muted);
  background-image:linear-gradient(45deg,transparent 50%,var(--muted) 50%),linear-gradient(135deg,var(--muted) 50%,transparent 50%);
  background-position:calc(100% - 17px) 55%,calc(100% - 12px) 55%;background-size:5px 5px;background-repeat:no-repeat}
.model:hover{border-color:var(--accent);color:var(--text)}
.model option{background:var(--surface);color:var(--text)}
.spacer{flex:1}
.status{font-size:12px;color:var(--faint)}
.status.bad{color:var(--danger)}

.scroll{flex:1;overflow-y:auto;scroll-behavior:smooth}
.wrap{max-width:780px;margin:0 auto;padding:8px 24px 150px}
.empty{height:60vh;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;gap:8px;animation:rise .5s ease both}
.empty .logo{width:54px;height:54px;border-radius:17px;font-size:26px;margin-bottom:10px}
.empty h1{font-size:28px;font-weight:600;letter-spacing:-.3px}
.empty p{color:var(--muted);font-size:13px;max-width:420px;overflow:hidden;text-overflow:ellipsis}

.msg{display:flex;gap:14px;margin:22px 0;animation:rise .3s ease both}
.msg.user{justify-content:flex-end}
.bubble{background:var(--bubble);padding:10px 16px;border-radius:18px 18px 6px 18px;max-width:82%;word-wrap:break-word;overflow-wrap:anywhere}
.avatar{flex:none;width:28px;height:28px;border-radius:9px;margin-top:2px;display:grid;place-items:center;color:#fff;font-size:13px;
  background:linear-gradient(135deg,var(--accent),var(--accent2))}
.col{flex:1;min-width:0}
.body{overflow-wrap:anywhere}
.body>*:first-child{margin-top:0}
.body p{margin:.6em 0}.body ul,.body ol{margin:.6em 0 .6em 1.3em}.body li{margin:.2em 0}
.body h2,.body h3,.body h4,.body h5{margin:1em 0 .4em;line-height:1.3;font-weight:600}
.body h2{font-size:1.3em}.body h3{font-size:1.15em}
.body a{color:var(--accent);text-decoration:none}.body a:hover{text-decoration:underline}
.body blockquote{border-left:3px solid var(--accent);padding:.1em 0 .1em 14px;color:var(--muted);margin:.7em 0}
.body hr{border:0;border-top:1px solid var(--line);margin:1.2em 0}
.body code{font:13px var(--mono);background:var(--code);padding:2px 6px;border-radius:6px;border:1px solid var(--line)}
.code{margin:.8em 0;border:1px solid var(--line);border-radius:12px;overflow:hidden;background:var(--code)}
.code-h{display:flex;justify-content:space-between;align-items:center;padding:6px 8px 6px 14px;font-size:12px;color:var(--faint);
  border-bottom:1px solid var(--line)}
.copy{color:var(--muted);padding:3px 9px;border-radius:7px;font-size:12px}.copy:hover{background:var(--raised);color:var(--text)}
.code pre{padding:12px 14px;overflow-x:auto}
.code code{background:none;border:0;padding:0;font:13px/1.55 var(--mono)}
.err{margin-top:8px;color:var(--danger);font-size:13px}
.acts{display:flex;gap:2px;margin-top:6px;opacity:0;transition:opacity .2s}
.msg:hover .acts,.msg:last-child .acts{opacity:1}
.acts button{color:var(--faint);padding:4px 8px;border-radius:8px;font-size:12px}.acts button:hover{background:var(--raised);color:var(--text)}

details.think{margin:2px 0 8px;color:var(--muted);font-size:13px}
details.think summary{cursor:pointer;list-style:none;display:inline-flex;align-items:center;gap:8px;padding:3px 10px;border-radius:999px;
  background:var(--surface);border:1px solid var(--line);user-select:none}
details.think summary::-webkit-details-marker{display:none}
details.think .tbody{margin-top:8px;padding-left:12px;border-left:2px solid var(--line);white-space:pre-wrap;max-height:260px;overflow:auto}
.dots{width:7px;height:7px;border-radius:50%;background:var(--faint)}
details.think.live .dots{background:var(--accent);animation:pulse 1.1s ease-in-out infinite}
.typing{display:inline-flex;gap:5px;padding:8px 0}
.typing i{width:7px;height:7px;border-radius:50%;background:var(--faint);animation:pulse 1.1s ease-in-out infinite}
.typing i:nth-child(2){animation-delay:.15s}.typing i:nth-child(3){animation-delay:.3s}

.composer{position:absolute;left:0;right:0;bottom:0;padding:0 24px 22px;
  background:linear-gradient(to top,var(--bg) 55%,transparent);pointer-events:none}
.box{pointer-events:auto;max-width:780px;margin:0 auto;display:flex;align-items:flex-end;gap:8px;background:var(--surface);
  border:1px solid var(--line);border-radius:22px;padding:8px 8px 8px 18px;box-shadow:var(--shadow);transition:border-color .2s,box-shadow .2s}
.box:focus-within{border-color:var(--accent);box-shadow:var(--shadow),0 0 0 3px color-mix(in srgb,var(--accent) 18%,transparent)}
textarea{flex:1;resize:none;border:0;outline:0;background:none;max-height:200px;padding:8px 0;line-height:1.5}
textarea::placeholder{color:var(--faint)}
.send{flex:none;width:38px;height:38px;border-radius:50%;display:grid;place-items:center;color:#fff;font-size:16px;
  background:linear-gradient(135deg,var(--accent),var(--accent2));transition:transform .15s,opacity .15s}
.send:hover{transform:scale(1.07)}.send:disabled{opacity:.35;transform:none;cursor:default}
.hint{text-align:center;color:var(--faint);font-size:11.5px;margin-top:8px;pointer-events:auto}

dialog{margin:auto;background:var(--surface);color:var(--text);border:1px solid var(--line);border-radius:18px;padding:22px;width:min(520px,92vw);box-shadow:var(--shadow)}
dialog::backdrop{background:rgba(0,0,0,.5);backdrop-filter:blur(3px)}
dialog h3{margin-bottom:6px}dialog p{color:var(--muted);font-size:13px;margin-bottom:12px}
dialog textarea{width:100%;min-height:130px;background:var(--bg);border:1px solid var(--line);border-radius:12px;padding:10px 12px}
.row{display:flex;justify-content:flex-end;gap:8px;margin-top:14px}
.btn{padding:8px 16px;border-radius:10px;background:var(--raised);border:1px solid var(--line)}.btn:hover{border-color:var(--accent)}
.btn.primary{background:linear-gradient(135deg,var(--accent),var(--accent2));color:#fff;border:0}

@keyframes rise{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}
@keyframes pulse{0%,100%{opacity:.3;transform:scale(.8)}50%{opacity:1;transform:scale(1)}}
@media (max-width:820px){.app{grid-template-columns:0 1fr}.wrap{padding:8px 14px 150px}.composer{padding:0 12px 14px}}
</style>
</head>
<body>
<div class="app" id="app">
  <aside class="side">
    <div class="brand"><div class="logo">✦</div>Мой ИИ</div>
    <button class="new" id="newChat"><span>＋</span> Новый чат</button>
    <div class="list" id="list"></div>
    <div class="side-foot" id="foot">Работает локально на вашем компьютере</div>
  </aside>
  <section class="main">
    <div class="top">
      <button class="icon" id="toggle" title="Боковая панель">☰</button>
      <select class="model" id="model" title="Модель"></select>
      <span class="spacer"></span>
      <span class="status" id="status"></span>
      <button class="icon" id="gear" title="Инструкция для модели">⚙</button>
    </div>
    <div class="scroll" id="scroll"><div class="wrap" id="msgs"></div></div>
    <div class="composer">
      <div class="box">
        <textarea id="input" rows="1" placeholder="Напишите сообщение…" autofocus></textarea>
        <button class="send" id="send" title="Отправить">↑</button>
      </div>
      <div class="hint">Enter — отправить, Shift+Enter — новая строка</div>
    </div>
  </section>
</div>

<dialog id="dlg">
  <h3>Инструкция для модели</h3>
  <p>Эта подсказка добавляется в начало каждого чата: например, как отвечать и каким тоном. Можно оставить пустой.</p>
  <textarea id="sys" placeholder="Например: Отвечай кратко и по-русски."></textarea>
  <div class="row"><button class="btn" id="dlgCancel">Отмена</button><button class="btn primary" id="dlgSave">Сохранить</button></div>
</dialog>

<script>
const TOKEN="__TOKEN__", DEFAULT_MODEL="__MODEL__";
const $=s=>document.querySelector(s);
const msgsEl=$('#msgs'), scrollEl=$('#scroll'), input=$('#input'), sendBtn=$('#send'), modelSel=$('#model'), statusEl=$('#status');
const store={get(k,d){try{const v=localStorage.getItem(k);return v===null?d:v}catch(e){return d}},set(k,v){try{localStorage.setItem(k,v)}catch(e){}}};
const uid=()=>Date.now().toString(36)+Math.random().toString(36).slice(2,6);
async function copyText(t){
  try{await navigator.clipboard.writeText(t);return true}
  catch(e){try{const a=document.createElement('textarea');a.value=t;a.style.cssText='position:fixed;opacity:0';document.body.append(a);a.select();const ok=document.execCommand('copy');a.remove();return ok}catch(_){return false}}
}
const el=(t,c,h)=>{const e=document.createElement(t);if(c)e.className=c;if(h!==undefined)e.innerHTML=h;return e};

async function api(path,opt={}){
  opt.headers=Object.assign({'X-App-Token':TOKEN,'Content-Type':'application/json'},opt.headers||{});
  const r=await fetch(path,opt);
  if(!r.ok){let t=await r.text(),m=t;try{m=JSON.parse(t).error||t}catch(e){}throw new Error(m)}
  return r.json();
}

/* ---------- Markdown (маленький и безопасный) ---------- */
const esc=s=>s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
function inline(s){
  const keep=[];
  s=s.replace(/`([^`\n]+)`/g,(_,c)=>{keep.push('<code>'+esc(c)+'</code>');return '\u0000'+(keep.length-1)+'\u0000'});
  s=esc(s);
  s=s.replace(/\*\*([^*\n]+)\*\*/g,'<strong>$1</strong>');
  s=s.replace(/(^|[^*\w])\*([^*\n]+)\*(?!\w)/g,'$1<em>$2</em>');
  s=s.replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g,'<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
  return s.replace(/\u0000(\d+)\u0000/g,(_,i)=>keep[+i]);
}
function codeBlock(code,lang){
  return '<div class="code"><div class="code-h"><span>'+esc(lang||'код')+'</span><button class="copy">Копировать</button></div><pre><code>'+esc(code)+'</code></pre></div>';
}
function md(src){
  const L=src.replace(/\r/g,'').split('\n'),out=[];let i=0;
  const isBlock=l=>/^(```|#{1,4}\s|\s*[-*]\s|\s*\d+[.)]\s|>)/.test(l);
  while(i<L.length){
    const l=L[i];let m;
    if((m=l.match(/^```(\S*)/))){const c=[];i++;while(i<L.length&&!/^```/.test(L[i]))c.push(L[i++]);i++;out.push(codeBlock(c.join('\n'),m[1]));continue}
    if((m=l.match(/^(#{1,4})\s+(.*)/))){const n=m[1].length+1;out.push('<h'+n+'>'+inline(m[2])+'</h'+n+'>');i++;continue}
    if(/^\s*[-*]\s+/.test(l)){const it=[];while(i<L.length&&/^\s*[-*]\s+/.test(L[i]))it.push(inline(L[i++].replace(/^\s*[-*]\s+/,'')));out.push('<ul>'+it.map(x=>'<li>'+x+'</li>').join('')+'</ul>');continue}
    if(/^\s*\d+[.)]\s+/.test(l)){const it=[];while(i<L.length&&/^\s*\d+[.)]\s+/.test(L[i]))it.push(inline(L[i++].replace(/^\s*\d+[.)]\s+/,'')));out.push('<ol>'+it.map(x=>'<li>'+x+'</li>').join('')+'</ol>');continue}
    if(/^>/.test(l)){const q=[];while(i<L.length&&/^>/.test(L[i]))q.push(L[i++].replace(/^>\s?/,''));out.push('<blockquote>'+inline(q.join(' '))+'</blockquote>');continue}
    if(/^\s*(---+|\*\*\*+)\s*$/.test(l)){out.push('<hr>');i++;continue}
    if(!l.trim()){i++;continue}
    const p=[];while(i<L.length&&L[i].trim()&&!isBlock(L[i]))p.push(L[i++]);
    if(!p.length)p.push(L[i++]);
    out.push('<p>'+inline(p.join('\n')).replace(/\n/g,'<br>')+'</p>');
  }
  return out.join('');
}

/* ---------- Состояние ---------- */
let chats=[],cur=null,busy=false,ctrl=null;

function splitThink(m){
  let think=m.thinking||'',ans=m.content||'';
  const t=ans.match(/^\s*<think>([\s\S]*?)(<\/think>|$)/);
  if(t){think=(think?think+'\n':'')+t[1];ans=ans.slice(t[0].length)}
  return {think:think.trim(),ans};
}
function fillAssistant(row,m,streaming){
  const {th,body,acts}=row._r,{think,ans}=splitThink(m);
  th.hidden=!think;
  th.classList.toggle('live',streaming&&!ans.trim()&&!!think);
  th.querySelector('.tlabel').textContent=(streaming&&!ans.trim())?'Думаю…':'Размышления';
  th.querySelector('.tbody').textContent=think;
  let html=ans.trim()?md(ans):((streaming&&!think)?'<span class="typing"><i></i><i></i><i></i></span>':'');
  if(m.error)html+='<div class="err">'+esc(m.error)+'</div>';
  body.innerHTML=html;
  acts.innerHTML='';
  if(!streaming&&ans.trim()){
    const c=el('button','','Копировать');c.onclick=async()=>{const ok=await copyText(ans.trim());c.textContent=ok?'Скопировано':'Не удалось';setTimeout(()=>c.textContent='Копировать',1200)};
    acts.append(c);
    const last=cur.messages[cur.messages.length-1]===m;
    if(last){const r=el('button','','Повторить');r.onclick=regenerate;acts.append(r)}
  }else if(!streaming&&m.error){
    const r=el('button','','Повторить');r.onclick=regenerate;acts.append(r);
  }
}
function addMsg(m){
  const row=el('div','msg '+m.role);
  if(m.role==='user'){row.append(el('div','bubble',esc(m.content).replace(/\n/g,'<br>')))}
  else{
    row.append(el('div','avatar','✦'));
    const col=el('div','col'),th=el('details','think','<summary><span class="dots"></span><span class="tlabel">Размышления</span></summary><div class="tbody"></div>'),
      body=el('div','body'),acts=el('div','acts');
    th.hidden=true;col.append(th,body,acts);row.append(col);row._r={th,body,acts};
    fillAssistant(row,m,false);
  }
  msgsEl.append(row);return row;
}
function renderChat(){
  msgsEl.innerHTML='';
  if(!cur.messages.length){
    msgsEl.innerHTML='<div class="empty"><div class="logo">✦</div><h1>Чем помочь?</h1><p>'+esc(modelSel.value||DEFAULT_MODEL)+'</p></div>';
  }else cur.messages.forEach(addMsg);
  renderSide();scrollDown(true);
}
function scrollDown(force){
  const near=scrollEl.scrollHeight-scrollEl.scrollTop-scrollEl.clientHeight<140;
  if(force||near)scrollEl.scrollTo({top:scrollEl.scrollHeight,behavior:force?'auto':'smooth'});
}
function renderSide(){
  const list=$('#list');list.innerHTML='';
  chats.forEach(c=>{
    const it=el('div','item'+(cur&&c.id===cur.id?' active':''));
    it.append(el('span','',esc(c.title)));
    const d=el('button','del','×');d.title='Удалить';
    d.onclick=async e=>{e.stopPropagation();if(!confirm('Удалить чат «'+c.title+'»?'))return;
      await api('/api/chats/'+c.id,{method:'DELETE'});chats=chats.filter(x=>x.id!==c.id);
      if(cur&&cur.id===c.id)newChat();else renderSide()};
    it.append(d);it.onclick=()=>openChat(c.id);list.append(it);
  });
}
function newChat(){
  cur={id:uid(),title:'Новый чат',model:modelSel.value,messages:[],updated:Date.now()};
  renderChat();input.focus();
}
async function openChat(id){
  if(busy)return;
  try{cur=await api('/api/chats/'+id)}catch(e){return}
  if(cur.model&&[...modelSel.options].some(o=>o.value===cur.model))modelSel.value=cur.model;
  renderChat();
}
async function save(){
  cur.updated=Date.now();
  try{
    await api('/api/chats/'+cur.id,{method:'PUT',body:JSON.stringify(cur)});
    chats=await api('/api/chats');renderSide();
  }catch(e){}
}

/* ---------- Отправка и потоковый ответ ---------- */
function setBusy(b){
  busy=b;sendBtn.textContent=b?'■':'↑';sendBtn.title=b?'Остановить':'Отправить';
  sendBtn.disabled=false;
}
function friendly(msg){
  if(/Failed to fetch|NetworkError/i.test(msg))return 'Потеряна связь с программой. Закройте окно и откройте приложение заново.';
  return msg;
}
async function generate(m){
  setBusy(true);ctrl=new AbortController();
  const row=msgsEl.lastElementChild;let pending=false,finished=false;
  const upd=()=>{if(pending)return;pending=true;requestAnimationFrame(()=>{pending=false;if(finished)return;fillAssistant(row,m,true);scrollDown()})};
  fillAssistant(row,m,true);
  try{
    const hist=cur.messages.slice(0,-1).map(x=>({role:x.role,content:x.content})).filter(x=>x.content.trim());
    const r=await fetch('/api/chat',{method:'POST',signal:ctrl.signal,headers:{'X-App-Token':TOKEN,'Content-Type':'application/json'},
      body:JSON.stringify({model:cur.model,messages:hist,system:store.get('sys','')})});
    if(!r.ok){let t=await r.text(),e=t;try{e=JSON.parse(t).error}catch(_){}throw new Error(e)}
    const rd=r.body.getReader(),dec=new TextDecoder();let buf='';
    for(;;){
      const {done,value}=await rd.read();if(done)break;
      buf+=dec.decode(value,{stream:true});let nl;
      while((nl=buf.indexOf('\n'))>=0){
        const line=buf.slice(0,nl).trim();buf=buf.slice(nl+1);if(!line)continue;
        let o;try{o=JSON.parse(line)}catch(_){continue}
        if(o.error)throw new Error(o.error);
        const mm=o.message||{};
        if(mm.thinking)m.thinking=(m.thinking||'')+mm.thinking;
        if(mm.content)m.content=(m.content||'')+mm.content;
        upd();
      }
    }
  }catch(e){if(e.name!=='AbortError')m.error=friendly(e.message||String(e))}
  finally{
    finished=true;ctrl=null;setBusy(false);fillAssistant(row,m,false);scrollDown();
    if(!m.content&&!m.thinking&&!m.error)cur.messages.pop(); // ничего не пришло (остановили сразу)
    await save();input.focus();
  }
}
async function send(){
  if(busy){if(ctrl)ctrl.abort();return}
  const text=input.value.trim();if(!text)return;
  input.value='';autosize();
  cur.model=modelSel.value;
  cur.messages.push({role:'user',content:text});
  if(cur.messages.length===1)cur.title=text.replace(/\s+/g,' ').slice(0,48);
  const m={role:'assistant',content:'',thinking:''};
  cur.messages.push(m);renderChat();
  await generate(m);
}
async function regenerate(){
  if(busy)return;
  const last=cur.messages[cur.messages.length-1];
  if(!last||last.role!=='assistant')return;
  cur.messages.pop();
  const m={role:'assistant',content:'',thinking:''};
  cur.messages.push(m);renderChat();
  await generate(m);
}

/* ---------- Интерфейс ---------- */
function autosize(){input.style.height='auto';input.style.height=Math.min(input.scrollHeight,200)+'px'}
input.addEventListener('input',autosize);
input.addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();send()}});
sendBtn.onclick=send;
$('#newChat').onclick=()=>{if(!busy)newChat()};
$('#toggle').onclick=()=>{const a=$('#app');a.classList.toggle('collapsed');store.set('collapsed',a.classList.contains('collapsed')?'1':'0')};
modelSel.onchange=()=>{store.set('model',modelSel.value);if(cur)cur.model=modelSel.value;if(!cur.messages.length)renderChat()};
msgsEl.addEventListener('click',async e=>{
  const b=e.target.closest('.copy');if(!b)return;
  const ok=await copyText(b.closest('.code').querySelector('code').textContent);
  b.textContent=ok?'Скопировано':'Не удалось';setTimeout(()=>b.textContent='Копировать',1200);
});
const dlg=$('#dlg');
$('#gear').onclick=()=>{$('#sys').value=store.get('sys','');dlg.showModal()};
$('#dlgCancel').onclick=()=>dlg.close();
$('#dlgSave').onclick=()=>{store.set('sys',$('#sys').value.trim());dlg.close()};
document.addEventListener('keydown',e=>{if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='n'){e.preventDefault();if(!busy)newChat()}});

async function init(){
  if(store.get('collapsed','0')==='1')$('#app').classList.add('collapsed');
  try{
    const r=await api('/api/models');
    if(r.offline){statusEl.textContent='Ollama не запущен';statusEl.classList.add('bad')}
    const names=r.models.length?r.models:[DEFAULT_MODEL];
    names.forEach(n=>modelSel.append(new Option(n,n)));
    const want=store.get('model',DEFAULT_MODEL);
    modelSel.value=names.includes(want)?want:(names.includes(DEFAULT_MODEL)?DEFAULT_MODEL:names[0]);
  }catch(e){modelSel.append(new Option(DEFAULT_MODEL,DEFAULT_MODEL))}
  try{chats=await api('/api/chats')}catch(e){chats=[]}
  newChat();
  const ping=()=>fetch('/api/ping',{method:'POST',headers:{'X-App-Token':TOKEN}}).catch(()=>{});
  ping();setInterval(ping,5000);
  window.addEventListener('beforeunload',()=>navigator.sendBeacon('/api/bye?t='+TOKEN));
}
init();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log(traceback.format_exc())
        raise
