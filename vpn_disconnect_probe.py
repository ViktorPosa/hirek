#!/usr/bin/env python3
"""
vpn_disconnect_probe.py  (v4 = first version, but every method first OPENS the
disconnect menu by clicking connectionCardActionButton).

Flow of EVERY method (single AppleScript run, no premature Escape):
    1. open the menu-bar popup (click menu bar item 1 of menu bar 2)
    2. click connectionCardActionButton  (this opens the disconnect menu)
    3. collect elements of ALL windows, then do the method's action there

RUN IN YOUR OWN TERMINAL:
    cd ~/Desktop/ScraperHU
    python3 vpn_disconnect_probe.py

- Enter = next method. Ctrl+C = quit. Don't touch mouse/keyboard while a
  method runs (~4s).
- Method 0 = DUMP: after opening the disconnect menu it lists every element —
  COPY THAT TO ME so I can target the exact Disconnect control.
- State checked by public IP (ground truth) + GUI status.
"""

import subprocess
import sys
import time
import json
import urllib.request

NORD_FP = ("nord", "tefincom", "packet", "packethub", "datacamp",
           "m247", "cdn77", "quadranet")

_HEADER = 'tell application "System Events"\n  tell process "NordVPN"\n'
_FOOTER = '\n  end tell\nend tell\n'

# Open popup + click connectionCardActionButton (opens disconnect menu) +
# gather elements of every window into `allUI`.
_PREFIX = '''    set mbi to menu bar item 1 of menu bar 2
    click mbi
    delay 2
    try
      repeat with oe in (entire contents of window 1)
        try
          if (value of attribute "AXIdentifier" of oe) is "connectionCardActionButton" then
            click oe
            exit repeat
          end if
        end try
      end repeat
    end try
    delay 2
    set allUI to {}
    repeat with win in (every window)
      try
        set allUI to allUI & (entire contents of win)
      end try
    end repeat
'''


def osa(body, timeout=40):
    script = _HEADER + _PREFIX + body + _FOOTER
    r = subprocess.run(["osascript", "-e", script],
                       capture_output=True, text=True, timeout=timeout)
    return (r.stdout or "").strip(), (r.stderr or "").strip()


def osa_raw(script, timeout=30):
    r = subprocess.run(["osascript", "-e", script],
                       capture_output=True, text=True, timeout=timeout)
    return (r.stdout or "").strip(), (r.stderr or "").strip()


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


_STATUS = '''
tell application "System Events"
  tell process "NordVPN"
    set mbi to menu bar item 1 of menu bar 2
    click mbi
    delay 1.5
    repeat with e in (entire contents of window 1)
      try
        if (value of attribute "AXIdentifier" of e) is "dashboard" then
          key code 53
          return help of e
        end if
      end try
    end repeat
    key code 53
    return "unknown"
  end tell
end tell
'''


def gui_status():
    out, err = osa_raw(_STATUS)
    if "Connected" in out and "Not" not in out:
        return "Connected"
    if "Not" in out or "Disconnected" in out:
        return "Disconnected"
    return out or (("ERR:" + err) if err else "unknown")


def show_state(label):
    time.sleep(3)
    ip, org = ip_org()
    verdict = "STILL NORD/VPN" if is_nord(org) else ">>> NOT NORD (disconnected!) <<<"
    print(f"\n  ==> STATE after {label}:")
    print(f"      Public IP : {ip} | org: {org}")
    print(f"      GUI status: {gui_status()}")
    print(f"      VERDICT   : {verdict}\n")


def pause(msg):
    try:
        input(f"[Enter] {msg}   (Ctrl+C to quit) ")
    except (EOFError, KeyboardInterrupt):
        print("\nbye")
        sys.exit(0)


# --- METHODS (bodies run with `allUI` already populated) --------------------

def m0_dump():
    body = '''    set out to ""
    repeat with e in allUI
      set ln to "-"
      try
        set ln to ln & " role=" & (role of e)
      end try
      try
        set ln to ln & " id=" & (value of attribute "AXIdentifier" of e)
      end try
      try
        set ln to ln & " desc=" & (description of e)
      end try
      try
        set ln to ln & " help=" & (help of e)
      end try
      try
        set ln to ln & " title=" & (title of e)
      end try
      set out to out & ln & linefeed
    end repeat
    return out'''
    out, err = osa(body)
    print("  --- elements after opening the disconnect menu ---")
    print(out if out else ("(empty / " + err + ")"))
    print("  --------------------------------------------------")


def _click_by_id(idlist):
    conds = " or ".join(['bid is "%s"' % i for i in idlist])
    body = '''    repeat with e in allUI
      try
        set bid to value of attribute "AXIdentifier" of e
        if %s then
          click e
          delay 1
          return "clicked id=" & bid
        end if
      end try
    end repeat
    return "not found"''' % conds
    out, err = osa(body)
    print("  result:", out or err)


def _click_by_text(subs):
    conds = " or ".join(['txt contains "%s"' % s for s in subs])
    body = '''    repeat with e in allUI
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
          click e
          delay 1
          return "clicked text=[" & txt & "]"
        end try
      end if
    end repeat
    return "not found"''' % conds
    out, err = osa(body)
    print("  result:", out or err)


def _coordclick_by_text(subs):
    conds = " or ".join(['txt contains "%s"' % s for s in subs])
    body = '''    repeat with e in allUI
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
          return "coord-clicked [" & txt & "] at " & (cx as integer) & "," & (cy as integer)
        end try
      end if
    end repeat
    return "not found"''' % conds
    out, err = osa(body)
    print("  result:", out or err)


def m1_id_candidates():
    _click_by_id(["disconnectButton", "disconnect", "pauseButton", "pause",
                  "snoozeButton", "snooze", "turnOff", "vpnDisconnect"])


def m2_text_disconnect():
    _click_by_text(["disconnect", "turn off", "kapcsol"])


def m3_text_pause_snooze():
    _click_by_text(["pause", "snooze", "sz\u00fcnet"])


def m4_coordclick_disconnect():
    _coordclick_by_text(["disconnect", "turn off", "pause", "snooze"])


def m5_keyboard():
    body = '''    key code 36
    delay 0.5
    key code 49
    delay 0.5
    key code 125
    delay 0.3
    key code 36
    return "sent Return, Space, Down, Return"'''
    out, err = osa(body)
    print("  result:", out or err)


def m6_deeplink():
    subprocess.run(["open", "nordvpn://disconnect"])
    print("  fired: open nordvpn://disconnect")


def m7_quit_app():
    print("  WARNING: Kill Switch on => internet may drop until relaunch.")
    out, err = osa_raw('tell application "NordVPN" to quit')
    print("  result:", out or err or "quit sent")


def m8_killall():
    print("  WARNING: hard kill.")
    r = subprocess.run(["killall", "NordVPN"], capture_output=True, text=True)
    print("  result:", (r.stderr or r.stdout or "killall sent").strip())


METHODS = [
    ("0  DUMP the opened disconnect menu   (★ paste this to me)", m0_dump),
    ("1  click by disconnect/pause AXIdentifier", m1_id_candidates),
    ("2  click element with text disconnect/turn off", m2_text_disconnect),
    ("3  click element with text pause/snooze", m3_text_pause_snooze),
    ("4  COORD-click element with text disconnect/pause", m4_coordclick_disconnect),
    ("5  keyboard in opened menu (Return/Space/Down/Return)", m5_keyboard),
    ("6  deep link nordvpn://disconnect (control)", m6_deeplink),
    ("7  quit NordVPN app (Kill-Switch warning)", m7_quit_app),
    ("8  killall NordVPN (last resort)", m8_killall),
]


def main():
    print("=" * 64)
    print("NordVPN disconnect probe v4 — every method opens the disconnect")
    print("menu (clicks connectionCardActionButton) BEFORE acting.")
    print("Method 0 = DUMP: copy its element list back to me.")
    print("=" * 64)
    ip, org = ip_org()
    print(f"Start: IP={ip} | org={org} | {'NORD/VPN' if is_nord(org) else 'not nord'}\n")

    for label, func in METHODS:
        pause(f"try method {label}")
        print(f"\n>>> METHOD {label}")
        try:
            func()
        except KeyboardInterrupt:
            print("\nbye")
            return
        except Exception as e:
            print("  method raised:", e)
        show_state(f"method {label.split()[0]}")

    print("Done. Paste method 0's dump, or tell me which number flipped to NOT NORD.")


if __name__ == "__main__":
    main()
