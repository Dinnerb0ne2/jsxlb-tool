# -*- coding: utf-8 -*-
"""一键开启: 定位->补丁->CA->hosts->代理->客户端 (由 start.bat 提权后执行)。"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
import ctl_common as C

def main():
    C.log("=== start ===")

    # 1. locate client
    d = C.client_dir()
    if not d:
        C.log("[!] client not found (set XLB_CLIENT_DIR to force)")
        return 1
    C.log("[1] client: %s" % d)

    # 2. kill stale client instances (prevents old-session / ghost windows)
    n = C.kill_client()
    if n:
        C.log("[2] killed %d stale client process(es)" % n)
    else:
        C.log("[2] no stale client process")

    # 3. patch asar if needed
    if C.asar_patched(d):
        C.log("[3] asar already patched")
    else:
        C.log("[3] patching asar ...")
        r = C.run([sys.executable, os.path.join(C.SRC, "patch_asar.py")])
        ok = C.asar_patched(d)
        C.log("[3] patch %s" % ("OK" if ok else "FAILED"))
        if not ok:
            C.log("[!] abort: asar patch failed; client would reject proxy certs")
            return 1

    # 4. CA trusted
    if C.ca_present():
        C.log("[4] CA already trusted")
    else:
        C.log("[4] adding CA to root store ...")
        C.log("[4] CA %s" % ("OK" if C.ca_add() else "FAILED"))

    # 5. hosts
    if C.hosts_has():
        C.log("[5] hosts already hijacked")
    else:
        C.log("[5] %s" % ("hosts hijacked" if C.hosts_add() else "hosts add FAILED"))

    # 6. daemon start (idempotent)
    # first ensure no orphan proxy holds 443/8100 (they are not daemon-managed)
    orphan = C.kill_port_owners()
    if orphan:
        C.log("[6] cleared orphan proxy: %s" % orphan)
        C.wait_port_free(443, timeout=10)
    C.daemon("stop")                       # ensure single managed instance
    C.wait_port_free(443, timeout=10)
    C.wait_port_free(8100, timeout=10)
    # optional manual upstream IP (rules/upstream_ip.txt) -> XLB_UPSTREAM_IP
    up_ip = C.upstream_ip_override()
    env = {"XLB_UPSTREAM_IP": up_ip} if up_ip else None
    if up_ip:
        C.log("[6] using manual upstream IP: %s" % up_ip)
    r = C.daemon("start", extra_env=env)
    C.log("[6] proxy daemon start issued")
    time.sleep(4)
    # verify listening
    if not C.wait_port_free(443, timeout=0):
        C.log("[6] proxy listening on 443: OK")
    else:
        C.log("[!] proxy NOT listening on 443")
        return 1

    # 7. launch client (silent)
    exe = C.client_exe(d)
    if exe:
        import subprocess
        DETACHED = 0x00000008 | 0x00000200
        subprocess.Popen([exe], cwd=d, close_fds=True, creationflags=DETACHED,
                         stdin=None, stdout=None, stderr=None)
        C.log("[7] client launched: %s" % os.path.basename(exe))
    else:
        C.log("[!] client exe not found")
        return 1

    C.log("=== start done (see logs/hijack_proxy.log for ws events) ===")
    return 0

if __name__ == "__main__":
    sys.exit(main())
