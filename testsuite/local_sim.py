#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
local_sim.py — 本地模拟客户端 (offline harness)
================================================================
一个零依赖(仅 aiohttp)的"教室小喇叭客户端"行为模拟器,用于安全测试。
它只实现与漏洞验证相关的最小行为子集,并遵循真实客户端 main.js 的逻辑。

安全边界 (见 README):
  - 仅监听 127.0.0.1, 拒绝一切非本机连接
  - 不携带任何真实凭据/真实 classId
  - 不访问 xlb.810086.com

模拟的行为 (与真实客户端行号对照):
  [GATE-1] 收到 broadcast.message(messageType=command) -> 记录命令执行意图
  [FILE-1] 收到 file 广播 -> path.join(downloadDir, fileName) 无清洗拼接
  [RCE-2]  收到 text 广播(enableTTS) -> 模拟 edge-tts exec 字符串拼接,检测注入
  [AUTH-2] 万能解锁码硬编码 810086
  [TLS-1]  标记 NODE_TLS_REJECT_UNAUTHORIZED 状态
运行: python local_sim.py [--port 8101]
"""
import argparse
import json
import os
import re
import sys
import time
import threading
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# 模拟状态
STATE = {
    "connected_server": None,      # 记录最近一次"注册"的服务器 origin
    "node_tls_reject_unauthorized": os.environ.get("NODE_TLS_REJECT_UNAUTHORIZED", "(unset)"),
    "processed_message_ids": [],   # 模拟 wasBroadcastProcessed
    "commands_executed": [],       # [GATE-1] 记录
    "file_writes": [],             # [FILE-1] 记录 (target, resolved_path, escaped_root)
    "tts_commands": [],            # [RCE-2] 记录
    "app_lock": {"active": False, "unlock_code": "", "master_code": "810086"},
}

DOWNLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sim_download_root")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# 真实客户端  master.js:1669: 支持环境变量重定向
SERVER_URL = os.environ.get("DESKTOP_BROADCAST_SERVER_URL", "https://xlb.810086.com")

# 模拟客户端内部逻辑 (1:1 对照真实源码)
def sim_handle_gateway_payload(payload):
    """对照 main.js:6478 handleGatewayPayload"""
    t = payload.get("type")
    if t != "broadcast.message":
        return {"handled": False}

    msg_id = str(payload.get("messageId") or payload.get("message_id") or "")
    # 对照 main.js:6384 wasBroadcastProcessed
    if msg_id in STATE["processed_message_ids"]:
        return {"handled": False, "deduped": True}
    STATE["processed_message_ids"].append(msg_id)
    STATE["processed_message_ids"] = STATE["processed_message_ids"][-500:]

    meta = payload.get("metadata") or {}
    msg_type = (payload.get("messageType") or payload.get("message_type")
                or meta.get("messageType") or "text")

    # 对照 main.js:6569-6590 command 分支 -> executeRemoteSystemControlAction
    if msg_type == "command":
        action = payload.get("command") or payload.get("action") or payload.get("title") or ""
        result = sim_execute_remote_system_control(action, payload)
        STATE["commands_executed"].append({
            "t": time.strftime("%H:%M:%S"), "action": action, "result": result})
        return {"handled": True, "branch": "command", "action": action, "result": result}

    # 对照 main.js:12478 processMessageData -> handleFileDownload (FILE-1)
    if msg_type == "file":
        file_name = payload.get("fileName") or ""
        return sim_handle_file_download(file_name)

    # 对照 renderer/banner.html:544 speakText -> tts-speak (RCE-2)
    if msg_type == "text" and meta.get("enableTTS"):
        text = payload.get("content", "")
        return sim_handle_tts(text)

    return {"handled": True, "branch": msg_type}

def sim_execute_remote_system_control(action, payload):
    """对照 main.js:4512 executeRemoteSystemControlAction"""
    if action == "lock_system":
        # main.js:4601 runSystemControlCommand('rundll32.exe user32.dll,LockWorkStation')
        return {"would_exec": "rundll32.exe user32.dll,LockWorkStation", "simulated": True}
    if action == "shutdown_system":
        # main.js:3325 120s 后 shutdown /s /t 0
        return {"would_exec": "shutdown.exe /s /t 0 (after 120s)", "simulated": True}
    if action in ("lock_app", "unlock_app"):
        code = payload.get("unlockCode", "")
        STATE["app_lock"] = {
            "active": action == "lock_app",
            "unlock_code": code,
            "master_code": "810086",  # main.js:4085 硬编码
        }
        return {"app_lock": STATE["app_lock"], "simulated": True}
    return {"unknown_action": True}

def sim_handle_file_download(file_name):
    """对照 main.js:12530: path.join(downloadPath, fileName) 无 basename 清洗"""
    import posixpath, ntpath
    # 模拟 path.join 在 Windows 下的行为: 直接拼接, '..' 不被拒绝
    resolved = os.path.normpath(os.path.join(DOWNLOAD_DIR, file_name.replace("/", os.sep)))
    escaped_root = not (resolved == DOWNLOAD_DIR or resolved.startswith(DOWNLOAD_DIR + os.sep))
    # 真实客户端这里会发起 http 下载并 createWriteStream —— 模拟器只记录,不写盘
    STATE["file_writes"].append({
        "t": time.strftime("%H:%M:%S"),
        "file_name": file_name,
        "resolved": resolved,
        "escaped_root": escaped_root,
    })
    return {"branch": "file", "file_name": file_name,
            "resolved": resolved, "path_traversal": escaped_root}

def sim_handle_tts(text):
    """对照 utils/edge-tts.js:138-141: 仅转义双引号,cmd.exe 下 \\\" 仍可闭合引号"""
    escaped = text.replace('"', '\\"')
    command = ('npx edge-tts --voice "zh-CN-XiaoxiaoNeural" --rate="+0%" '
               '--volume="+100%" --text "%s" --write-media "C:/temp/tts.mp3"' % escaped)
    # 检测: cmd.exe 元字符是否逃逸出引号包裹的 --text 参数
    # 真实 cmd 解析: \" 关闭引号 -> 之后的 & | 等是命令分隔符
    # 逐字符状态机模拟 cmd 引号解析
    in_quote = False
    outside_quote_chars = []
    i = 0
    while i < len(command):
        c = command[i]
        if c == '\\' and i + 1 < len(command) and command[i+1] == '"':
            # \\\" -> cmd 不把它当转义,视为关闭引号
            in_quote = not in_quote
            outside_quote_chars.append('\\"')
            i += 2
            continue
        if c == '"':
            in_quote = not in_quote
        elif not in_quote:
            outside_quote_chars.append(c)
        i += 1
    outside = "".join(outside_quote_chars)
    injected = bool(re.search(r'[&|`^]', outside))
    STATE["tts_commands"].append({
        "t": time.strftime("%H:%M:%S"),
        "raw_text": text,
        "escaped": escaped,
        "cmd_would_inject": injected,
        "outside_quote_segment": outside[:200],
    })
    return {"branch": "tts", "cmd_would_inject": injected, "command": command[:200]}

# HTTP 服务 (仅 127.0.0.1)
class SimHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        sys.stderr.write("[sim] %s\n" % (fmt % args))

    def _client_ok(self):
        return self.client_address[0] in ("127.0.0.1", "::1", "localhost")

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self._client_ok():
            self._json({"error": "local simulator only - non-loopback refused"}, 403)
            return
        path = urllib.parse.urlparse(self.path).path
        if path == "/":
            self._json({"sim": "jsxlb local client simulator",
                        "server_url_env": SERVER_URL,
                        "note": "POST /ws 下一帧网关 payload; GET /state 看模拟状态"})
        elif path == "/state":
            self._json(STATE)
        elif path == "/reset":
            for k in ("processed_message_ids", "commands_executed", "file_writes", "tts_commands"):
                STATE[k] = []
            STATE["app_lock"] = {"active": False, "unlock_code": "", "master_code": "810086"}
            self._json({"ok": True})
        elif path == "/register":
            # 模拟客户端设备注册时暴露的信息 (对照 main.js:2114)
            self._json({"sim_device_register": {
                "server_origin": SERVER_URL,
                "tls_reject_unauthorized": STATE["node_tls_reject_unauthorized"],
                "note": "真实客户端用 Bearer classToken 注册;token 值不在此模拟",
            }})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        if not self._client_ok():
            self._json({"error": "local simulator only"}, 403)
            return
        path = urllib.parse.urlparse(self.path).path
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            self._json({"error": "bad json"}, 400)
            return
        if path == "/ws":
            result = sim_handle_gateway_payload(payload)
            self._json({"sim_result": result})
        elif path == "/verify/master-code":
            code = str(payload.get("code", ""))
            # 模拟 main.js:4085 万能码
            ok = code == STATE["app_lock"].get("master_code") \
                or (STATE["app_lock"].get("unlock_code") and code == STATE["app_lock"]["unlock_code"])
            self._json({"unlocked": bool(ok), "match": "master" if code == "810086" else "teacher" if ok else "none"})
        else:
            self._json({"error": "not found"}, 404)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8101)
    args = parser.parse_args()
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), SimHandler)
    print("[sim] local client simulator on http://127.0.0.1:%d (loopback only)" % args.port)
    print("[sim] download root (simulated): %s" % DOWNLOAD_DIR)
    print("[sim] NODE_TLS_REJECT_UNAUTHORIZED=%s  DESKTOP_BROADCAST_SERVER_URL=%s"
          % (STATE["node_tls_reject_unauthorized"], SERVER_URL))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[sim] bye")

if __name__ == "__main__":
    main()
