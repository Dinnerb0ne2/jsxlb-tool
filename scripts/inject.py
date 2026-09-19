# -*- coding: utf-8 -*-
"""帧注入 CLI — 不经过教师端, 直接向已连接的教室大屏推帧。

  banner <文本> [--sender 名] [--tts on|off] [--seconds N] [--mode banner|fullscreen]
  safety <标题> <内容> [--seconds N]     每日安全全屏播报 (displayMode=daily_safety_fullscreen)
  teacher <标题> <内容> [--seconds N]    班主任寄语全屏 (displayMode=head_teacher_message_fullscreen)
  command <动作>                         lock_system / shutdown_system / lock_app / unlock_app
  raw <json | @文件 | ->                 原样发帧 (最自由; - = 从 stdin 读)
  clients                                在线会话 + 捕获概况
  frames [n] [--clear]                   查看/清空代理捕获环

注入帧默认原样下发; 加 --apply-rules 则也过规则引擎 (会被 replace / block 影响)。
"""
import argparse
import json
import sys
import urllib.error
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

PORT = 8100


def url(path):
    return "http://127.0.0.1:%d%s" % (PORT, path)


def get(path):
    with urllib.request.urlopen(url(path), timeout=8) as r:
        return json.load(r)


def post(path, obj):
    req = urllib.request.Request(url(path),
                                 data=json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=8) as r:
        return json.load(r)


def api_fail(e):
    print("[!] 代理不可达 (127.0.0.1:%d): %s" % (PORT, e))
    print("    先启动: start.bat  或  py -3 scripts\\hijack_daemon.py start")
    return 2


def send(body):
    r = post("/__inject", body)
    if r.get("dropped"):
        print("[i] 注入被规则丢弃: %s" % r.get("reason"))
        return 0
    sent = r.get("sent_to", 0)
    print("[+] 已注入: %d 个会话" % sent)
    if not sent:
        print("[i] 当前没有客户端连在代理上 (py -3 scripts\\hijack_daemon.py status 看状态)")
    print("    帧: %s" % json.dumps(r.get("frame"), ensure_ascii=False)[:300])
    return 0


def cmd_banner(a):
    body = {"text": a.text, "apply_rules": a.apply_rules}
    if a.sender:
        body["sender"] = a.sender
    if a.tts is not None:
        body["enableTTS"] = (a.tts == "on")
    if a.seconds:
        body["seconds"] = a.seconds
    if a.mode == "fullscreen":
        body["displayMode"] = "fullscreen"
    return send(body)


def cmd_safety(a):
    body = {"text": a.content, "title": a.title, "source": "daily_safety",
            "displayMode": "daily_safety_fullscreen", "apply_rules": a.apply_rules}
    if a.seconds:
        body["seconds"] = a.seconds
    return send(body)


def cmd_teacher(a):
    body = {"text": a.content, "title": a.title, "source": "head_teacher_message",
            "displayMode": "head_teacher_message_fullscreen", "apply_rules": a.apply_rules}
    if a.seconds:
        body["seconds"] = a.seconds
    return send(body)


def cmd_command(a):
    return send({"command": a.action, "apply_rules": a.apply_rules})


def cmd_raw(a):
    if a.json == "-":
        body = json.load(sys.stdin)
    elif a.json.startswith("@"):
        with open(a.json[1:], encoding="utf-8") as f:
            body = json.load(f)
    else:
        body = json.loads(a.json)
    if a.apply_rules and isinstance(body, dict):
        body["apply_rules"] = True
    return send(body)


def cmd_clients(a):
    s = get("/__status")
    print("clients=%d  capture=%d  upstream=%s (%s)  passthrough=%s"
          % (s.get("clients", 0), s.get("capture", 0),
             s.get("upstream"), s.get("upstreamIp"), s.get("passthrough")))
    if not s.get("clients"):
        print("[i] 没有客户端连在代理上")
    return 0


def cmd_frames(a):
    path = "/__frames?n=%d" % a.n + ("&clear=1" if a.clear else "")
    r = get(path)
    print("# 捕获 %d 条, 显示 %d 条%s" % (r.get("count", 0), len(r.get("frames", [])),
                                        (" (已清空 %d)" % r.get("cleared")) if a.clear else ""))
    for e in r.get("frames", []):
        key = e.get("content") or e.get("command") or e.get("fileName") or ""
        print("%s %-6s %-8s %-24s %s" % (e.get("t"), e.get("dir"), e.get("action"),
                                         (e.get("type") or "-"), str(key)[:60]))
    return 0


def main():
    global PORT
    ap = argparse.ArgumentParser(
        prog="inject.py",
        description="帧注入: 直接向已连接的教室大屏推帧 (不经教师端)")
    ap.add_argument("--port", type=int, default=8100, help="代理明文端口 (默认 8100)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("banner", help="推一条横幅")
    b.add_argument("text")
    b.add_argument("--sender", default="", help="发件人显示名 (自动按教师卡片渲染)")
    b.add_argument("--tts", choices=["on", "off"], default=None, help="强制开/关语音朗读")
    b.add_argument("--seconds", type=int, default=0, help="自动关闭秒数")
    b.add_argument("--mode", choices=["banner", "fullscreen"], default="banner")
    b.add_argument("--apply-rules", action="store_true", help="注入帧也过规则引擎")
    b.set_defaults(fn=cmd_banner)

    s = sub.add_parser("safety", help="每日安全全屏播报")
    s.add_argument("title")
    s.add_argument("content")
    s.add_argument("--seconds", type=int, default=0)
    s.add_argument("--apply-rules", action="store_true")
    s.set_defaults(fn=cmd_safety)

    t = sub.add_parser("teacher", help="班主任寄语全屏")
    t.add_argument("title")
    t.add_argument("content")
    t.add_argument("--seconds", type=int, default=0)
    t.add_argument("--apply-rules", action="store_true")
    t.set_defaults(fn=cmd_teacher)

    c = sub.add_parser("command", help="远程控制命令")
    c.add_argument("action", help="lock_system / shutdown_system / lock_app / unlock_app ...")
    c.add_argument("--apply-rules", action="store_true")
    c.set_defaults(fn=cmd_command)

    r = sub.add_parser("raw", help="原样发帧 (json 串 / @文件 / - = stdin)")
    r.add_argument("json")
    r.add_argument("--apply-rules", action="store_true")
    r.set_defaults(fn=cmd_raw)

    sub.add_parser("clients", help="在线会话 + 捕获概况").set_defaults(fn=cmd_clients)

    f = sub.add_parser("frames", help="查看/清空捕获环")
    f.add_argument("n", nargs="?", type=int, default=30)
    f.add_argument("--clear", action="store_true")
    f.set_defaults(fn=cmd_frames)

    a = ap.parse_args()
    PORT = a.port
    try:
        return a.fn(a)
    except urllib.error.URLError as e:
        return api_fail(e)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
