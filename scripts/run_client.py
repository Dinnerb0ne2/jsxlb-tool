# -*- coding: utf-8 -*-
"""静默启动客户端 (自动定位安装目录), 并把客户端输出写入日志。

客户端 stdout/stderr -> logs/client_console.log (追加, 保留历史)。
"""
import os
import sys
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from client_locator import locate_or_die, _pick_exe
import oplog

DETACHED = 0x00000008 | 0x00000200
CLIENT_LOG = os.path.join(ROOT, "logs", "client_console.log")


def main():
    d = locate_or_die()
    exe = _pick_exe(d)
    if not exe:
        print("[!] 安装目录找到了 (%s) 但没有可执行文件" % d)
        oplog.op("run_client", "FAIL no exe in %s" % d)
        sys.exit(1)
    os.makedirs(os.path.dirname(CLIENT_LOG), exist_ok=True)
    logf = open(CLIENT_LOG, "a", encoding="utf-8", errors="replace")
    logf.write("\n---- client start %s ----\n" % __import__("time").strftime("%Y-%m-%d %H:%M:%S"))
    logf.flush()
    env = dict(os.environ)
    env["ELECTRON_ENABLE_LOGGING"] = "1"
    subprocess.Popen([exe], cwd=d, close_fds=True, env=env,
                     creationflags=DETACHED, stdin=None,
                     stdout=logf, stderr=subprocess.STDOUT)
    print("[+] client launched: %s" % exe)
    print("    client log: %s" % CLIENT_LOG)
    oplog.op("run_client", "launched %s" % os.path.basename(exe))


if __name__ == "__main__":
    oplog.op("run_client", "start %s" % oplog.run_arg())
    main()
