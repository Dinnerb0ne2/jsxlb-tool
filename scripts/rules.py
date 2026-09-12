# -*- coding: utf-8 -*-
"""规则命令行工具 (免手写 JSON / 免转义)。

  show | get <路径> | set <路径> <值> | add <路径> <值...>
  del <路径> <索引> | rm <路径> | flag <路径> on|off | reload | apply | reset
"""
import os
import sys
import json
import copy

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES_FILE = os.path.join(ROOT, "rules", "hijack_rules.json")
API = "http://127.0.0.1:8100/__rules"

DEFAULT = {
    "debug": {"log_frames": False, "dump_dir": "", "dry_run": False},
    "banner": {"types": ["text", "banner", "notice", "popup"], "block_banner": False,
               "replace": [], "remove": [], "append": "", "force_sender": "", "force_tts": None},
    "seat": {"exclude": [], "only": [], "pairs": []},
    "timer": {"force_seconds": 0},
    "commands": {"block": [], "block_snapshot": False},
    "files": {"block_traversal": True, "block": []},
    "block_ws_types": ["renderer.pack.push"],
    "block_paths": [],
    "passthrough": False,
}

def log(msg):
    print(msg, flush=True)

def load_file():
    if os.path.isfile(RULES_FILE):
        with open(RULES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return copy.deepcopy(DEFAULT)

def save_file(rules):
    os.makedirs(os.path.dirname(RULES_FILE), exist_ok=True)
    tmp = RULES_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rules, f, ensure_ascii=False, indent=2)
    os.replace(tmp, RULES_FILE)

def parse_value(s):
    """智能解析: true/false/null/数字/JSON 数组或对象; 否则当字符串 (不丢中文)。"""
    t = s.strip()
    low = t.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    if low in ("null", "none"):
        return None
    try:
        return json.loads(t)          # 数字 / 数组 / 对象 / 带引号字符串
    except Exception:
        return s                      # 原样字符串 (中文/空格保留)

def get_path(rules, path):
    node = rules
    for part in path.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return None, False
    return node, True

def set_path(rules, path, value):
    parts = path.split(".")
    node = rules
    for p in parts[:-1]:
        if not isinstance(node.get(p), dict):
            node[p] = {}
        node = node[p]
    node[parts[-1]] = value

def push_rules(rules):
    import urllib.request
    try:
        req = urllib.request.Request(
            API, data=json.dumps(rules, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=8) as r:
            json.load(r)
        log("[+] 已热更新到运行中的代理")
        return True
    except Exception as e:
        log("[i] 代理未运行或不可达 (%s) — 规则已存文件, 代理启动时生效" % type(e).__name__)
        return False

def load_live_or_file():
    """优先从代理读当前生效规则, 失败则读文件。"""
    import urllib.request
    try:
        with urllib.request.urlopen(API, timeout=5) as r:
            return json.load(r), "live(handle)"
    except Exception:
        return load_file(), "file"

def main():
    if len(sys.argv) < 2:
        log(__doc__)
        return 0
    cmd = sys.argv[1].lower()
    args = sys.argv[2:]

    if cmd == "show":
        rules, src = load_live_or_file()
        log("# 当前规则 (来源: %s)" % src)
        log(json.dumps(rules, ensure_ascii=False, indent=2))
        return 0

    if cmd == "get":
        if not args:
            log("[!] 用法: rules.py get <点路径>"); return 1
        rules, _ = load_live_or_file()
        v, ok = get_path(rules, args[0])
        log(json.dumps(v, ensure_ascii=False) if ok else "(未设置)")
        return 0

    if cmd == "set":
        if len(args) < 2:
            log("[!] 用法: rules.py set <点路径> <值>"); return 1
        rules = load_file()
        val = parse_value(" ".join(args[1:]))
        set_path(rules, args[0], val)
        save_file(rules)
        log("[+] %s = %s" % (args[0], json.dumps(val, ensure_ascii=False)))
        push_rules(rules)
        return 0

    if cmd == "flag":
        if len(args) < 2:
            log("[!] 用法: rules.py flag <点路径> on|off"); return 1
        rules = load_file()
        val = args[1].lower() in ("on", "true", "1", "yes", "y")
        set_path(rules, args[0], val)
        save_file(rules)
        log("[+] %s = %s" % (args[0], val))
        push_rules(rules)
        return 0

    if cmd == "add":
        if len(args) < 2:
            log("[!] 用法: rules.py add <点路径> <值> [值2 ...]"); return 1
        rules = load_file()
        arr, ok = get_path(rules, args[0])
        if not ok or not isinstance(arr, list):
            arr = []
        vals = [parse_value(v) for v in args[1:]]
        item = vals[0] if len(vals) == 1 else vals
        arr.append(item)
        set_path(rules, args[0], arr)
        save_file(rules)
        log("[+] %s += %s" % (args[0], json.dumps(item, ensure_ascii=False)))
        push_rules(rules)
        return 0

    if cmd == "del":
        if len(args) < 2:
            log("[!] 用法: rules.py del <点路径> <索引>"); return 1
        rules = load_file()
        arr, ok = get_path(rules, args[0])
        if not ok or not isinstance(arr, list):
            log("[!] 该键不是数组"); return 1
        try:
            idx = int(args[1])
            removed = arr.pop(idx)
        except Exception as e:
            log("[!] 索引无效: %s" % e); return 1
        save_file(rules)
        log("[+] %s 删除 [%d] = %s" % (args[0], idx, json.dumps(removed, ensure_ascii=False)))
        push_rules(rules)
        return 0

    if cmd == "rm":
        if not args:
            log("[!] 用法: rules.py rm <点路径>"); return 1
        rules = load_file()
        v, ok = get_path(rules, args[0])
        empty = [] if isinstance(v, list) else ({} if isinstance(v, dict)
                else (0 if isinstance(v, int) else ("" if isinstance(v, str) else None)))
        set_path(rules, args[0], empty)
        save_file(rules)
        log("[+] %s 已清空" % args[0])
        push_rules(rules)
        return 0

    if cmd == "reload":
        rules, _ = load_live_or_file()   # 不需要, 直接推文件
        rules = load_file()
        log("[i] 推 %s" % RULES_FILE)
        push_rules(rules)
        return 0

    if cmd == "apply":
        rules = load_file()
        push_rules(rules)
        return 0

    if cmd == "reset":
        if os.path.isfile(RULES_FILE):
            import shutil
            shutil.copy2(RULES_FILE, RULES_FILE + ".bak")
            log("[i] 旧规则已备份为 hijack_rules.json.bak")
        save_file(copy.deepcopy(DEFAULT))
        log("[+] 已恢复默认规则模板")
        push_rules(DEFAULT)
        return 0

    log("[!] 未知命令: %s\n%s" % (cmd, __doc__))
    return 1

if __name__ == "__main__":
    sys.exit(main())
