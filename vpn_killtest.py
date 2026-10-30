#!/usr/bin/env python3
"""
vpn_killtest.py — test the "turn off -> start" strategy WITHOUT any GUI/AX
clicking (so it works from any context). Records the public IP at each stage:

  1. baseline
  2. after killing the NordVPN app  (does the tunnel drop?)
  3. after relaunching NordVPN       (does it auto-reconnect?)

Ground truth = public IP org (NordVPN exits: M247/PacketHub/Datacamp/etc.).
Writes to stdout; run with output redirected to a log.
"""

import subprocess
import time
import json
import urllib.request
import datetime

NORD_FP = ("nord", "tefincom", "packet", "packethub", "datacamp",
           "m247", "cdn77", "quadranet")


def log(m):
    print(f"[{datetime.datetime.now():%H:%M:%S}] {m}", flush=True)


def ip_org():
    try:
        req = urllib.request.Request("https://ipinfo.io/json",
                                     headers={"User-Agent": "curl/8"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            d = json.loads(resp.read().decode("utf-8", "replace"))
        return d.get("ip", "?"), d.get("org", "?")
    except Exception as e:
        return "?", "(lookup failed: %s)" % e


def is_nord(org):
    return any(f in (org or "").lower() for f in NORD_FP)


def stage(label, wait):
    time.sleep(wait)
    ip, org = ip_org()
    tag = "NORD" if is_nord(org) else "NOT NORD (no VPN)"
    log(f"{label}: IP={ip} | org={org} -> {tag}")
    return not is_nord(org)


def running():
    r = subprocess.run(["pgrep", "-x", "NordVPN"], capture_output=True, text=True)
    return r.stdout.strip()


def main():
    log(f"NordVPN app PIDs: {running() or '(none)'}")
    stage("baseline", 0)

    log("-> killall NordVPN (turn off app)")
    subprocess.run(["killall", "NordVPN"], capture_output=True, text=True)
    time.sleep(2)
    log(f"NordVPN app PIDs after kill: {running() or '(none)'}")
    dropped = stage("after killall (t+6s)", 6)
    if not dropped:
        dropped = stage("after killall (t+15s)", 9)

    if dropped:
        log("RESULT: killing the app DROPPED the tunnel (disconnect works via kill).")
    else:
        log("RESULT: tunnel still up after killing app (system extension keeps it).")

    log("-> open -a NordVPN (start app again)")
    subprocess.run(["open", "-a", "NordVPN"], capture_output=True, text=True)
    time.sleep(6)
    log(f"NordVPN app PIDs after relaunch: {running() or '(none)'}")
    reconn = not stage("after relaunch (t+8s)", 8)
    stage("after relaunch (t+20s)", 12)
    log("RESULT: %s" % ("app auto-reconnected on launch"
                         if reconn else "app launched but did NOT auto-connect"))
    log("DONE.")


if __name__ == "__main__":
    main()
