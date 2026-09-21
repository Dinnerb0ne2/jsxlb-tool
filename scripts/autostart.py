# -*- coding: utf-8 -*-
"""开机自启: 让劫持链路跟着教室客户端一起开机起来 (start.bat 注册, end.bat 注销)。

  install        注册开机自启 (计划任务: SYSTEM + 最高权限 + 开机触发)
  remove         注销
  status         查看注册状态
  print-command  只打印将被注册的命令 (核对用)
  run            由计划任务在开机时调用: 自愈 hosts/CA/asar + 拉起代理

要点
- 任务名 `jsxlb-hijack-boot`。SYSTEM + HIGHEST + ONSTART: 开机即跑, 不用等登录,
  不弹 UAC; 教室客户端由它自己的开机自启负责 —— 本脚本不杀不拉客户端。
- `run` 是"自愈"入口: 开机后依次确认 hosts / CA / asar 补丁 / 代理, 缺哪个补哪个。
- `end.bat` 会注销任务, 系统回到干净状态 (下次开机不再拉起)。
"""
import argparse
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import oplog
import ctl_common as C

TASK = "jsxlb-hijack-boot"
BOOT_LOG = os.path.join(ROOT, "logs", "boot.log")
PY = sys.executable
SELF = os.path.join(ROOT, "scripts", "autostart.py")


def boot_log(msg):
    line = "[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    try:
        os.makedirs(os.path.dirname(BOOT_LOG), exist_ok=True)
        with open(BOOT_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass
    print(line, flush=True)


def task_command():
    """注册进计划任务的完整命令 (python + 本脚本 run)。"""
    return '"%s" "%s" run' % (PY, SELF)


def install():
    cmd = task_command()
    r = C.run(["schtasks", "/Create", "/TN", TASK, "/TR", cmd,
               "/SC", "ONSTART", "/RU", "SYSTEM", "/RL", "HIGHEST", "/F"])
    ok = bool(r and r.returncode == 0)
    if ok:
        print("[+] 开机自启已注册: %s" % TASK)
        print("    命令: %s" % cmd)
        print("    触发: 开机 (SYSTEM, 最高权限, 免登录免 UAC)")
        print("    自检: py -3 scripts\\autostart.py status")
    else:
        out = ((r.stdout or "") + (r.stderr or "")).strip() if r else "schtasks 调用失败"
        print("[!] 注册失败: %s" % out[:300])
        print("    需要管理员权限 (start.bat 自带提权; 手动跑请用管理员)")
    C.log("[autostart] install %s" % ("OK" if ok else "FAILED"))
    return 0 if ok else 1


def remove():
    r = C.run(["schtasks", "/Delete", "/TN", TASK, "/F"])
    ok = bool(r and r.returncode == 0)
    print("[%s] 开机自启%s" % ("+" if ok else "=", "已注销" if ok else "本来就没注册"))
    C.log("[autostart] remove %s" % ("OK" if ok else "not-present"))
    return 0


def status():
    r = C.run(["schtasks", "/Query", "/TN", TASK, "/V", "/FO", "LIST"])
    if r and r.returncode == 0:
        print("[+] 已注册")
        for line in (r.stdout or "").splitlines():
            line = line.strip()
            if any(k in line for k in ("TaskName", "任务名", "Task To Run", "要运行的任务",
                                       "Run As User", "以用户身份运行", "Schedule Type", "计划类型",
                                       "Status", "状态", "Next Run", "下次运行")):
                print("    %s" % line)
    else:
        print("[=] 未注册 (双击 start.bat 会自动注册, 或 autostart.py install)")
    return 0


def print_command():
    print(task_command())
    return 0


def run_boot():
    """开机自愈: hosts -> CA -> asar -> 代理。不碰客户端。"""
    boot_log("=== boot guard start ===")
    ok = True

    if C.hosts_has():
        boot_log("[1] hosts 已劫持")
    else:
        boot_log("[1] hosts %s" % ("已补写" if C.hosts_add() else "补写失败"))
        ok = ok and C.hosts_has()

    if C.ca_present():
        boot_log("[2] CA 已信任")
    else:
        boot_log("[2] CA %s" % ("已补装" if C.ca_add() else "补装失败"))

    d = C.client_dir()
    if not d:
        boot_log("[3] 找不到客户端目录 (asar 跳过; 客户端装完再跑 start.bat 即可)")
    elif C.asar_patched(d):
        boot_log("[3] asar 补丁在位")
    else:
        C.run([PY, os.path.join(ROOT, "src", "patch_asar.py")])
        patched = C.asar_patched(d)
        boot_log("[3] asar %s" % ("已重打补丁" if patched else "补丁失败"))
        ok = ok and patched

    envr = C.run([PY, os.path.join(ROOT, "scripts", "envcheck.py"), "--ensure"])
    boot_log("[3.5] 依赖预检 %s" % ("OK" if (envr and envr.returncode == 0)
                                    else "失败 (跑 scripts\\envcheck.py 看详情)"))

    orphans = C.kill_port_owners()
    if orphans:
        boot_log("[4] 清理孤儿代理: %s" % orphans)
    C.daemon("stop")
    C.wait_port_free(443, timeout=10)
    up_ip = C.upstream_ip_override()
    env = {"XLB_UPSTREAM_IP": up_ip} if up_ip else None
    C.daemon("start", extra_env=env)
    if C.wait_port_open(443, timeout=20):
        boot_log("[4] 代理已在线 (443)")
    else:
        boot_log("[4] 代理未监听 443 (看 logs/hijack_proxy.log)")
        ok = False

    boot_log("=== boot guard %s ===" % ("OK" if ok else "有失败项"))
    C.log("[autostart] boot run %s" % ("OK" if ok else "FAILED"))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(prog="autostart.py",
                                 description="开机自启: 让劫持链路跟着教室客户端一起起来")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("install", help="注册开机自启 (需管理员)").set_defaults(fn=lambda a: install())
    sub.add_parser("remove", help="注销开机自启 (需管理员)").set_defaults(fn=lambda a: remove())
    sub.add_parser("status", help="查看注册状态").set_defaults(fn=lambda a: status())
    sub.add_parser("print-command", help="打印将被注册的命令").set_defaults(fn=lambda a: print_command())
    sub.add_parser("run", help="开机入口: 自愈并拉起代理").set_defaults(fn=lambda a: run_boot())
    a = ap.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    oplog.op("run", oplog.run_arg())
    sys.exit(main())
