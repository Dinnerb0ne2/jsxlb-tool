# -*- coding: utf-8 -*-
"""代理事件流监听: 只看改写/封锁/注入/连接这类关键行 (tail -f)。

  py -3 scripts/watch.py             回看 20 行后持续监听
  py -3 scripts/watch.py --n 0       只看新事件
  py -3 scripts/watch.py --all       看全部行 (含逐帧日志)
  py -3 scripts/watch.py --grep 横幅  额外关键词过滤
"""
import argparse
import os
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(ROOT, "logs", "hijack_proxy.log")
KEYS = ("BANNER", "BLOCK", "DROPPED", "TIMER", "SEAT", "STUDENTS", "INJECT",
        "client connected", "session ended", "upstream:", "RULES updated",
        "pump error", "connect failed", "证书验证")


def main():
    ap = argparse.ArgumentParser(prog="watch.py", description="代理事件流监听 (tail -f, 自动过滤)")
    ap.add_argument("--n", type=int, default=20, help="启动时回看行数 (0 = 只看新事件)")
    ap.add_argument("--all", action="store_true", help="不过滤, 显示全部行")
    ap.add_argument("--grep", default="", help="额外关键词过滤")
    a = ap.parse_args()

    def keep(line):
        if a.grep and a.grep not in line:
            return False
        return a.all or any(k in line for k in KEYS)

    if not os.path.isfile(LOG):
        print("[!] 还没有日志: %s (先 start.bat)" % LOG)
        return 2

    f = open(LOG, encoding="utf-8", errors="replace")
    if a.n:
        # 只读末尾 256KB, 避免整文件读入
        try:
            f.seek(max(0, os.path.getsize(LOG) - 262144))
            f.readline()
        except OSError:
            pass
        tail = [ln for ln in f.read().splitlines() if keep(ln)]
        for line in tail[-a.n:]:
            print(line, flush=True)
    f.seek(0, os.SEEK_END)
    print("# 监听中 (Ctrl-C 退出): %s" % LOG, flush=True)
    try:
        while True:
            line = f.readline()
            if line:
                line = line.rstrip("\n")
                if keep(line):
                    print(line, flush=True)
            else:
                time.sleep(0.5)
    except KeyboardInterrupt:
        print("")
        print("# 已退出")
    finally:
        f.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
