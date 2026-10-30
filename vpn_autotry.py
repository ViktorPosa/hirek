#!/usr/bin/env python3
"""
vpn_autotry.py — automatically try several NordVPN disconnect techniques and
log which one actually drops the tunnel (verified by public IP). Stops at the
first success so it never toggles the VPN back on.

Writes everything to stdout (redirect to a log file). No interactive input.
Safe techniques only (no quit/killall).
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


def osa(script, timeout=40):
    try:
        r = subprocess.run(["osascript", "-e", script],
                           capture_output=True, text=True, timeout=timeout)
        return (r.stdout or "").strip(), (r.stderr or "").strip()
    except Exception as e:
        return "", f"EXC: {e}"


def ip_org():
    try:
        req = urllib.request.Request("https://ipinfo.io/json",
                                     headers={"User-Agent": "curl/8"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            d = json.loads(resp.read().decode("utf-8", "replace"))
        return d.get("ip", "?"), d.get("org", "?")
    except Exception as e:
        return "?", str(e)


def is_nord(org):
    return any(f in (org or "").lower() for f in NORD_FP)


_OPEN_GATHER = '''
    set mbi to menu bar item 1 of menu bar 2
    click mbi
    delay 2
    set allUI to {}
    repeat with win in (every window)
      try
        set allUI to allUI & (entire contents of win)
      end try
    end repeat
'''


def _wrap(body):
    return ('tell application "System Events"\n  tell process "NordVPN"\n'
            + _OPEN_GATHER + body + '\n  end tell\nend tell\n')


def coord_click_id(target):
    body = '''
    repeat with e in allUI
      try
        if (value of attribute "AXIdentifier" of e) is "%s" then
          set pos to position of e
          set sz to size of e
          set cx to (item 1 of pos) + ((item 1 of sz) / 2)
          set cy to (item 2 of pos) + ((item 2 of sz) / 2)
          click at {cx, cy}
          delay 1
          return "coordclick %s at " & (cx as integer) & "," & (cy as integer)
        end if
      end try
    end repeat
    return "not found: %s"''' % (target, target, target)
    return osa(_wrap(body))


def axpress_id(target):
    body = '''
    repeat with e in allUI
      try
        if (value of attribute "AXIdentifier" of e) is "%s" then
          perform action "AXPress" of e
          delay 1
          return "axpress %s"
        end if
      end try
    end repeat
    return "not found: %s"''' % (target, target, target)
    return osa(_wrap(body))


def more_then_text_coordclick(subs):
    """Coord-click the '...' MORE button, then coord-click a revealed element
    whose text matches subs."""
    conds = " or ".join(['txt contains "%s"' % s for s in subs])
    body = '''
    -- coord-click the MORE button first
    repeat with e in allUI
      try
        if (value of attribute "AXIdentifier" of e) is "connectionCardMoreButton" then
          set pos to position of e
          set sz to size of e
          set cx to (item 1 of pos) + ((item 1 of sz) / 2)
          set cy to (item 2 of pos) + ((item 2 of sz) / 2)
          click at {cx, cy}
          exit repeat
        end if
      end try
    end repeat
    delay 2
    -- re-gather (a menu may have appeared)
    set allUI2 to {}
    repeat with win in (every window)
      try
        set allUI2 to allUI2 & (entire contents of win)
      end try
    end repeat
    try
      repeat with mnu in (every menu)
        try
          set allUI2 to allUI2 & (entire contents of mnu)
        end try
      end repeat
    end try
    repeat with e in allUI2
      set txt to ""
      try
        set txt to txt & (description of e) & " "
      end try
      try
        set txt to txt & (help of e) & " "
      end try
      try
        set txt to txt & (title of e)
      end try
      if %s then
        try
          set pos to position of e
          set sz to size of e
          set cx to (item 1 of pos) + ((item 1 of sz) / 2)
          set cy to (item 2 of pos) + ((item 2 of sz) / 2)
          click at {cx, cy}
          delay 1
          return "coordclicked MORE-item [" & txt & "]"
        end try
      end if
    end repeat
    return "MORE: no disconnect/pause item found"''' % conds
    return osa(_wrap(body))


METHODS = [
    ("A coord-click connectionCardActionButton",
     lambda: coord_click_id("connectionCardActionButton")),
    ("B MORE(...) then coord-click disconnect/pause/turn off item",
     lambda: more_then_text_coordclick(["disconnect", "turn off", "pause", "snooze"])),
    ("C AXPress connectionCardActionButton (control)",
     lambda: axpress_id("connectionCardActionButton")),
]


def check(label):
    time.sleep(4)
    ip, org = ip_org()
    if is_nord(org):
        log(f"   AFTER {label}: IP={ip} org={org} -> STILL NORD")
        return False
    log(f"   AFTER {label}: IP={ip} org={org} -> NOT NORD  ✅ DISCONNECTED")
    return True


def main():
    ip, org = ip_org()
    log(f"BEFORE: IP={ip} org={org} -> {'NORD' if is_nord(org) else 'NOT NORD'}")
    if not is_nord(org):
        log("Already not on NordVPN — nothing to test. Abort.")
        return
    for name, fn in METHODS:
        log(f">>> METHOD {name}")
        out, err = fn()
        log(f"   osa: {out or ('ERR:' + err)}")
        if check(name):
            log(f"WINNER: METHOD {name}")
            return
    log("No method disconnected. Next step: install cliclick for a real HW click.")


if __name__ == "__main__":
    main()
