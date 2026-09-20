# -*- coding: utf-8 -*-
"""一键恢复: 停代理->关客户端->还原asar->删CA->清hosts (由 end.bat 提权后执行)。"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "src"))
import oplog
import os
import sys
import shutil
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
import ctl_common as C

def main():
    C.log("=== end ===")

    # 0. 注销开机自启 (先做: 即使后面某步失败, 下次开机也不会再拉起)
    r = C.run([sys.executable, os.path.join(C.SCRIPTS, "autostart.py"), "remove"])
    C.log("[0] 开机自启%s" % ("已注销" if (r and r.returncode == 0) else "未注册/注销失败"))

    # 1. stop proxy via daemon, then kill any orphan listeners
    C.daemon("stop")
    time.sleep(1)
    killed = C.kill_port_owners()
    if killed:
        C.log("[1] killed orphan proxy process(es): %s" % killed)
    else:
        C.log("[1] no orphan proxy listener")
    C.log("[1] proxy stopped")

    # 2. kill client
    n = C.kill_client()
    C.log("[2] killed %d client process(es)" % n)

    # 3. restore asar from backup
    d = C.client_dir()
    if d:
        asar = os.path.join(d, "resources", "app.asar")
        bak = asar + ".bak"
        if os.path.isfile(bak):
            try:
                C.set_readonly(asar, False)      # clear read-only attr first
                time.sleep(0.3)
                shutil.copy2(bak, asar)
                C.set_readonly(asar, True)       # restore original attr
                C.log("[3] asar restored from backup")
            except Exception as e:
                C.log("[!] asar restore failed: %s" % e)
        else:
            C.log("[3] no asar backup, nothing to restore")
    else:
        C.log("[!] client not found, skip asar restore")

    # 4. remove CA from root store
    if C.ca_present():
        C.ca_remove()
        C.log("[4] CA removed")
    else:
        C.log("[4] CA not present")

    # 5. clean hosts
    if C.hosts_has():
        C.log("[5] %s" % ("hosts cleaned" if C.hosts_remove() else "hosts clean FAILED"))
    else:
        C.log("[5] hosts already clean")

    C.log("=== end done: system restored, client connects directly to real server ===")
    return 0

if __name__ == "__main__":
    oplog.op("run", oplog.run_arg())
    sys.exit(main())
