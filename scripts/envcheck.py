# -*- coding: utf-8 -*-
"""运行环境预检: 解释器 + 依赖 + 端口。

  py -3 scripts/envcheck.py                  # 只检查, 缺什么给什么命令
  py -3 scripts/envcheck.py --ensure         # 缺依赖就自动 pip install -r requirements.txt
  py -3 scripts/envcheck.py --modules a,b    # 指定要检查的模块 (默认 aiohttp,cryptography)

为什么需要: 工具链是"整目录拷贝可迁移"的, 但运行依赖装在**解释器**里。换机器/换 Python 时
代理会在启动瞬间死在 ModuleNotFoundError, 而 start.bat 只看到 "proxy NOT listening on 443"。
这里把原因说清楚, 并支持一条命令补齐。
"""
import argparse
import importlib
import os
import socket
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REQ = os.path.join(ROOT, "requirements.txt")
DEFAULT_MODULES = ("aiohttp", "cryptography")

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def log(msg):
    print(msg, flush=True)


def check(modules):
    """返回缺失模块列表; 每个模块打一行结论。"""
    missing = []
    for m in modules:
        try:
            mod = importlib.import_module(m)
            log("  [OK]   %-14s %s" % (m, getattr(mod, "__version__", "?")))
        except Exception as e:
            missing.append(m)
            log("  [MISS] %-14s %s" % (m, e))
    return missing


def ensure(missing):
    """用当前解释器 pip install -r requirements.txt, 装完复核。"""
    if not missing:
        return True
    if not os.path.isfile(REQ):
        log("[!] 找不到 requirements.txt: %s" % REQ)
        return False
    log("[i] 自动补装依赖: %s -m pip install -r requirements.txt" % sys.executable)
    try:
        r = subprocess.run([sys.executable, "-m", "pip", "install",
                            "--disable-pip-version-check", "-r", REQ],
                           capture_output=True, text=True, errors="replace", timeout=900)
    except Exception as e:
        log("[!] pip 调用失败: %s" % e)
        return False
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    if out:
        print(out[-1500:])
    if r.returncode != 0:
        log("[!] pip 安装失败 (校园网常挡 PyPI; 可换源或离线拷 whl 再 pip install <文件>)")
        return False
    return not check(missing)


def port_state(port):
    s = socket.socket()
    s.settimeout(1)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    except Exception:
        return False
    finally:
        s.close()


def main():
    ap = argparse.ArgumentParser(prog="envcheck.py", description="运行环境预检 (解释器/依赖/端口)")
    ap.add_argument("--ensure", action="store_true", help="缺依赖就自动 pip install -r requirements.txt")
    ap.add_argument("--modules", default=",".join(DEFAULT_MODULES), help="要检查的模块, 逗号分隔")
    a = ap.parse_args()

    mods = [m.strip() for m in a.modules.split(",") if m.strip()]
    log("解释器 : %s" % sys.executable)
    log("版本   : %s" % sys.version.split()[0])
    log("依赖   :")
    missing = check(mods)
    if a.ensure and missing:
        if ensure(missing):
            missing = []

    busy443, busy8100 = port_state(443), port_state(8100)
    log("端口   : 443 %s | 8100 %s"
        % ("使用中" if busy443 else "空闲", "使用中" if busy8100 else "空闲"))

    if missing:
        log("[!] 结论: 缺依赖 %s" % ", ".join(missing))
        log("    补齐: py -3 -m pip install -r requirements.txt")
        log("    或:   py -3 scripts\\envcheck.py --ensure")
        return 1
    log("[OK] 结论: 环境可用")
    return 0


if __name__ == "__main__":
    sys.exit(main())
