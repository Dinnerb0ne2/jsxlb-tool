#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""集成测试: 假上游 + 真代理 + 真 WS 客户端 (全回环, 不碰 hosts/CA/443)。

验证三件事 (跑不通 = 注入/改写/捕获链路坏了):
  1. 下行改写: 假上游发横幅 -> 客户端收到改写后的文本
     (复刻 WRITEUP 7 回放: "你好这是一次测试" -> "这不是测试喵~")
  2. 帧注入:   POST /__inject -> 客户端立即收到注入帧
  3. 捕获环:   GET /__frames 里能看到 rewrite / inject 记录

运行: py -3 testsuite/test_inject.py
"""
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time

from aiohttp import web
import aiohttp

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
HTTP_PORT = 18100
UP_PORT = 18101

RULES = {
    "debug": {"log_frames": False, "dump_dir": "", "dry_run": False, "capture_max": 50},
    "banner": {"types": ["text"], "block_banner": False,
               "replace": [["你好这是一次测试", "这不是测试"]], "remove": [],
               "append": "喵~",
               "force_sender": "", "force_tts": None},
    "seat": {"exclude": [], "only": [], "pairs": []},
    "timer": {"force_seconds": 0},
    "commands": {"block": [], "block_snapshot": False},
    "files": {"block_traversal": True, "block": []},
    "block_ws_types": [],
    "block_paths": [],
    "passthrough": False,
}


def make_rules():
    fd, path = tempfile.mkstemp(prefix="xlb_rules_", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(RULES, f, ensure_ascii=False)
    return path


async def upstream_ws(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    await ws.send_str(json.dumps({
        "type": "broadcast.message", "messageType": "text",
        "messageId": "u1", "content": "你好这是一次测试"}, ensure_ascii=False))
    async for _msg in ws:          # 挂着不动, 让代理的双向泵继续跑
        pass
    return ws


async def wait_http(session, url, timeout=20):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            async with session.get(url) as r:
                if r.status == 200:
                    return await r.json()
        except Exception:
            pass
        await asyncio.sleep(0.3)
    raise RuntimeError("代理没起来: %s" % url)


async def main():
    rules_path = make_rules()
    cert_dir = tempfile.mkdtemp(prefix="xlb_certs_")

    up = web.Application()
    up.router.add_route("GET", "/ws", upstream_ws)
    up_runner = web.AppRunner(up)
    await up_runner.setup()
    await web.TCPSite(up_runner, "127.0.0.1", UP_PORT).start()

    proxy = subprocess.Popen(
        [PY, os.path.join(ROOT, "src", "hijack_proxy.py"),
         "--host", "127.0.0.1", "--tls-port", "0", "--http-port", str(HTTP_PORT),
         "--upstream", "http://127.0.0.1:%d" % UP_PORT,
         "--upstream-ip", "127.0.0.1", "--rules", rules_path, "--cert-dir", cert_dir],
        cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        encoding="utf-8", errors="replace")

    ok = False
    banner_line = "?"
    try:
        async with aiohttp.ClientSession() as s:
            await wait_http(s, "http://127.0.0.1:%d/__status" % HTTP_PORT)
            async with s.ws_connect("ws://127.0.0.1:%d/ws" % HTTP_PORT) as cli:
                # 1. 下行改写 (WRITEUP 7 回放)
                msg = await asyncio.wait_for(cli.receive(), 10)
                got = json.loads(msg.data)
                assert got.get("messageId") == "u1", got
                assert got.get("content") == "这不是测试喵~", got
                banner_line = got.get("content")

                # 2. 帧注入
                inj = await s.post("http://127.0.0.1:%d/__inject" % HTTP_PORT,
                                   json={"text": "注入测试", "sender": "王老师", "seconds": 20})
                r = await inj.json()
                assert r.get("ok") and r.get("sent_to") == 1, r
                msg2 = await asyncio.wait_for(cli.receive(), 10)
                got2 = json.loads(msg2.data)
                assert got2.get("content") == "注入测试", got2
                assert got2.get("messageType") == "text" and got2.get("sender") == "王老师", got2

                # 3. 状态 + 捕获环
                st = await (await s.get("http://127.0.0.1:%d/__status" % HTTP_PORT)).json()
                assert st.get("clients") == 1, st
                fr = await (await s.get("http://127.0.0.1:%d/__frames?n=20" % HTTP_PORT)).json()
                acts = [e["action"] for e in fr.get("frames", [])]
                assert "rewrite" in acts and "inject" in acts, acts

                # 4. CLI 壳 (argparse -> HTTP) 可用性
                cli_clients = subprocess.run(
                    [PY, os.path.join(ROOT, "scripts", "inject.py"),
                     "--port", str(HTTP_PORT), "clients"],
                    capture_output=True, text=True, encoding="utf-8",
                    errors="replace", timeout=30, cwd=ROOT)
                assert cli_clients.returncode == 0, cli_clients.stdout + cli_clients.stderr
                assert "clients=1" in cli_clients.stdout, cli_clients.stdout

                # 5. CLI 注入 + --append (传统尾缀)
                cli_banner = subprocess.run(
                    [PY, os.path.join(ROOT, "scripts", "inject.py"),
                     "--port", str(HTTP_PORT), "banner", "CLI 注入", "--append", " 喵~",
                     "--sender", "王老师"],
                    capture_output=True, text=True, encoding="utf-8",
                    errors="replace", timeout=30, cwd=ROOT)
                assert cli_banner.returncode == 0, cli_banner.stdout + cli_banner.stderr
                assert "已注入: 1" in cli_banner.stdout, cli_banner.stdout
                msg3 = await asyncio.wait_for(cli.receive(), 10)
                got3 = json.loads(msg3.data)
                assert got3.get("content") == "CLI 注入 喵~", got3
        ok = True
    finally:
        proxy.terminate()
        try:
            out, _ = proxy.communicate(timeout=10)
        except Exception:
            proxy.kill()
            out = ""
        await up_runner.cleanup()
        for p in (rules_path,):
            try:
                os.remove(p)
            except OSError:
                pass

    if ok:
        print("[test_inject] PASS  banner rewrite: '你好这是一次测试' -> %r" % banner_line)
        print("[test_inject]       (下行改写 / 帧注入 / 捕获环 全部通过)")
        return 0
    print("[test_inject] FAIL")
    print("---- proxy output ----")
    print((out or "").strip()[-2000:])
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
