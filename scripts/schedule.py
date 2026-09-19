# -*- coding: utf-8 -*-
"""课表调度 CLI — 导入课表 / 看状态 / 演练强制状态 / 离线查时刻。

  py -3 scripts/schedule.py import <file.json>   导入课表 (校验 -> rules/timetable.json)
  py -3 scripts/schedule.py status               当前状态 (优先问运行中的代理)
  py -3 scripts/schedule.py periods              列出解析后的课节与课间
  py -3 scripts/schedule.py check [HH:MM] [--date YYYY-MM-DD]   离线推算某时刻
  py -3 scripts/schedule.py force auto|class|break|off          演练: 强制状态

调度效果: 三个弹窗 banner(横幅) / popup(小弹窗) / fullscreen(全屏) 按 schedule.targets
策略显示; 课上被压下的弹窗 fold(课间自动补发) 或 drop。字段参考 docs/RULES_REFERENCE.md。
"""
import argparse
import datetime
import json
import os
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import oplog
import hijack_proxy as hp

RULES_FILE = os.path.join(ROOT, "rules", "hijack_rules.json")
TIMETABLE_FILE = os.path.join(ROOT, "rules", "timetable.json")
API = "http://127.0.0.1:8100/__rules"

LABEL = {"class": "上课中", "break": "课间", "off": "课表外"}


def load_rules_file():
    if not os.path.isfile(RULES_FILE):
        return {"schedule": {}}
    with open(RULES_FILE, encoding="utf-8") as f:
        return json.load(f)


def save_rules_file(rules):
    tmp = RULES_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rules, f, ensure_ascii=False, indent=2)
    os.replace(tmp, RULES_FILE)


def push(rules):
    """热更新到运行中的代理; 代理不在就只存文件。"""
    try:
        req = urllib.request.Request(API, data=json.dumps(rules, ensure_ascii=False).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=8) as r:
            json.load(r)
        return True
    except Exception:
        return False


def _print_state(sch):
    label = LABEL.get(sch.get("state"), sch.get("state"))
    name = (" %s" % sch["name"]) if sch.get("name") else ""
    nxt = (" 下个节点 %s" % sch["next"]) if sch.get("next") else ""
    reason = (" [%s]" % sch["reason"]) if sch.get("reason") else ""
    print("状态: %s%s%s%s   enabled=%s" % (label, name, nxt, reason, sch.get("enabled")))


def cmd_status(_a):
    try:
        with urllib.request.urlopen("http://127.0.0.1:8100/__status", timeout=5) as r:
            st = json.load(r)
        print("代理: 在线  clients=%s  capture=%s  foldQueue=%s"
              % (st.get("clients"), st.get("capture"), st.get("foldQueue")))
        _print_state(st.get("schedule") or {})
    except Exception:
        print("代理: 离线 (状态为本地推算)")
        _print_state(hp.schedule_state())
    return 0


def cmd_periods(_a):
    conf = hp._schedule_conf()
    wds = conf.get("weekdays") or [1, 2, 3, 4, 5]
    print("# 上课星期: %s   调度: %s   blocked_action=%s"
          % (",".join(str(w) for w in wds), conf.get("enabled"), conf.get("blocked_action")))
    periods = hp._norm_periods(conf.get("periods"))
    for p in periods:
        print("  课节 %-14s %s-%s" % (p["name"] or "-", hp._fmt_hhmm(p["s"]), hp._fmt_hhmm(p["e"])))
    for b in hp._norm_periods(conf.get("breaks")):
        print("  课间 %-14s %s-%s" % (b["name"] or "-", hp._fmt_hhmm(b["s"]), hp._fmt_hhmm(b["e"])))
    if not conf.get("breaks"):
        for i in range(len(periods) - 1):
            p1, p2 = periods[i], periods[i + 1]
            if p2["s"] > p1["e"]:
                print("  课间 %-14s %s-%s (自动: 课节空隙)"
                      % ("-", hp._fmt_hhmm(p1["e"]), hp._fmt_hhmm(p2["s"])))
    if not periods:
        print("  (课表为空 — 先 schedule.py import <file.json>)")
    return 0


def cmd_check(a):
    if a.time:
        try:
            h, m = [int(x) for x in a.time.split(":")[:2]]
        except Exception:
            print("[!] 时间格式: HH:MM")
            return 1
        d = a.date or datetime.date.today().isoformat()
        try:
            y, mo, dd = [int(x) for x in d.split("-")]
            now = datetime.datetime(y, mo, dd, h, m)
        except Exception:
            print("[!] 日期格式: YYYY-MM-DD")
            return 1
    else:
        now = datetime.datetime.now()
    print("# 推算时刻: %s (星期%s)" % (now.strftime("%Y-%m-%d %H:%M"), now.isoweekday()))
    _print_state(hp.schedule_state(now))
    return 0


def cmd_force(a):
    rules = load_rules_file()
    rules.setdefault("schedule", {})["force"] = a.mode
    save_rules_file(rules)
    hp.deep_merge(hp.RULES, {"schedule": {"force": a.mode}})
    ok = push(rules)
    print("[+] schedule.force = %s %s" % (a.mode, "(已热更新)" if ok else "(代理未运行, 已存文件)"))
    _print_state(hp.schedule_state())
    return 0


def cmd_toggle(a):
    rules = load_rules_file()
    rules.setdefault("schedule", {})["enabled"] = (a.cmd == "on")
    save_rules_file(rules)
    hp.deep_merge(hp.RULES, {"schedule": {"enabled": a.cmd == "on"}})
    ok = push(rules)
    print("[+] schedule.enabled = %s %s" % (a.cmd == "on",
                                            "(已热更新)" if ok else "(代理未运行, 已存文件)"))
    return 0


def cmd_import(a):
    try:
        with open(a.file, encoding="utf-8") as f:
            tt = json.load(f)
    except Exception as e:
        print("[!] 读不了课表文件: %s" % e)
        return 1
    if not isinstance(tt, dict) or not isinstance(tt.get("periods"), list) or not tt["periods"]:
        print('[!] 课表格式不对: 需要 {"periods": [{"name":"第1节","start":"08:00","end":"08:45"}, ...]}')
        return 1
    periods = hp._norm_periods(tt.get("periods"))
    if not periods:
        print("[!] 没有任何可解析的课节 (start/end 要形如 08:00, 且 end > start)")
        return 1
    with open(TIMETABLE_FILE, "w", encoding="utf-8") as f:
        json.dump(tt, f, ensure_ascii=False, indent=2)
    rules = load_rules_file()
    sch = rules.setdefault("schedule", {})
    sch["timetable_file"] = "rules/timetable.json"
    sch["enabled"] = True
    save_rules_file(rules)
    hp.deep_merge(hp.RULES, {"schedule": {"timetable_file": "rules/timetable.json", "enabled": True}})
    ok = push(rules)
    print("[+] 已导入 %d 节课 -> %s" % (len(periods), TIMETABLE_FILE))
    for p in periods:
        print("     %-14s %s-%s" % (p["name"] or "-", hp._fmt_hhmm(p["s"]), hp._fmt_hhmm(p["e"])))
    print("[+] schedule.enabled=true %s" % ("(已热更新)" if ok else "(代理未运行, 启动时生效)"))
    print("    策略在 rules/hijack_rules.json 的 schedule.targets (banner/popup/fullscreen)")
    return 0


def main():
    ap = argparse.ArgumentParser(prog="schedule.py", description="课表调度: 三个弹窗按课表显示")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="当前状态 + 折叠队列").set_defaults(fn=cmd_status)
    sub.add_parser("periods", help="列出解析后的课节与课间").set_defaults(fn=cmd_periods)

    c = sub.add_parser("check", help="离线推算某时刻状态")
    c.add_argument("time", nargs="?", default="")
    c.add_argument("--date", default="")
    c.set_defaults(fn=cmd_check)

    f = sub.add_parser("force", help="演练: 强制状态")
    f.add_argument("mode", choices=["auto", "class", "break", "off"])
    f.set_defaults(fn=cmd_force)

    i = sub.add_parser("import", help="导入课表 JSON")
    i.add_argument("file")
    i.set_defaults(fn=cmd_import)

    sub.add_parser("on", help="开启课表调度").set_defaults(fn=cmd_toggle)
    sub.add_parser("off", help="关闭课表调度 (保留课表)").set_defaults(fn=cmd_toggle)

    a = ap.parse_args()
    try:
        hp.deep_merge(hp.RULES, load_rules_file())
    except Exception:
        pass
    return a.fn(a)


if __name__ == "__main__":
    oplog.op("run", oplog.run_arg())
    sys.exit(main())
