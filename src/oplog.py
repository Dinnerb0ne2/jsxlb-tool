# -*- coding: utf-8 -*-
"""统一操作日志: 所有脚本的每次运行都追加到 logs/operations.log。

无论通过 bat 还是直接运行, 都会留下: 时间 / 脚本名 / 动作 / 参数。
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(ROOT, "logs", "operations.log")

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def op(action, detail=""):
    """追加一条操作记录。永不抛异常 (日志失败不能影响主流程)。"""
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write("[%s] %-18s %s %s\n" % (
                time.strftime("%Y-%m-%d %H:%M:%S"),
                os.path.basename(sys.argv[0]),
                action,
                detail))
    except OSError:
        pass


def run_arg():
    """本次命令行参数 (用于记录)"""
    return " ".join(sys.argv[1:])
