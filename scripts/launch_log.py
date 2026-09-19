# -*- coding: utf-8 -*-
"""带日志启动客户端 (排障用), 日志写 logs/client_console.log。"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "src"))
import oplog
import os
import sys
import glob
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from client_locator import locate_or_die, _pick_exe

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(os.path.dirname(HERE), "logs", "client_console.log")
os.makedirs(os.path.dirname(LOG), exist_ok=True)

def main():
    d = locate_or_die()
    exe = _pick_exe(d)
    if not exe:
        print("[!] no client exe in", d)
        sys.exit(1)
    env = dict(os.environ)
    env["ELECTRON_ENABLE_LOGGING"] = "1"
    logf = open(LOG, "w", encoding="utf-8", errors="replace")
    p = subprocess.Popen([exe, "--enable-logging"], env=env, cwd=d,
                         stdout=logf, stderr=subprocess.STDOUT)
    print("[+] client launched with logging (pid %d) -> %s" % (p.pid, LOG))

if __name__ == "__main__":
    oplog.op("run", oplog.run_arg())
    main()
