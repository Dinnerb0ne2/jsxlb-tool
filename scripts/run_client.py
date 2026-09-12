# -*- coding: utf-8 -*-
"""静默启动客户端 (自动定位安装目录)。"""
import os
import sys
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from client_locator import locate_or_die, _pick_exe

DETACHED = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP

def main():
    d = locate_or_die()
    exe = _pick_exe(d)
    if not exe:
        print("[!] 安装目录找到了 (%s) 但没有可执行文件" % d)
        sys.exit(1)
    subprocess.Popen([exe], cwd=d, close_fds=True,
                     creationflags=DETACHED, stdin=None, stdout=None, stderr=None)
    print("[+] client launched (detached): %s" % exe)

if __name__ == "__main__":
    main()
