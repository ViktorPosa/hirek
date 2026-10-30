#!/usr/bin/env python3
"""
vpn_click_test.py — minimal, focused test.

We KNOW the disconnect control is the AXButton "connectionCardActionButton",
and that AppleScript `click`/AXPress on it does NOT disconnect (it just closes
the popup). This test does a REAL hardware-style coordinate click
(`click at {x, y}`) at the button's centre while the menu is open — that is the
technique most likely to actually actuate a SwiftUI button.

RUN IN YOUR OWN TERMINAL:
    cd ~/Desktop/ScraperHU
    python3 vpn_click_test.py

It prints the button coordinates, does the click, then checks your public IP.
If org stops being NordVPN (M247/PacketHub/etc.), it worked — tell me and I
wire this exact technique into vpn_control.py. If it still shows NordVPN, tell
me and we'll switch to a real mouse-event tool (cliclick / pyobjc-Quartz).
Don't touch the mouse/keyboard during the ~3s the menu is open.
"""

import subprocess
import time
import json
import urllib.request

NORD_FP = ("nord", "tefincom", "packet", "packethub", "datacamp",
           "m247", "cdn77", "quadranet")


def osa(script, timeout=30):
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


def verdict(org):
    return "STILL NORD/VPN" if any(f in (org or "").lower() for f in NORD_FP) \
        else ">>> NOT NORD (disconnected!) <<<"


# Open popup, locate the action button, REAL-click its centre.
CLICK_SCRIPT = '''
tell application "System Events"
  tell process "NordVPN"
    set mbi to menu bar item 1 of menu bar 2
    click mbi
    delay 2
    set target to missing value
    repeat with e in (entire contents of window 1)
      try
        if (value of attribute "AXIdentifier" of e) is "connectionCardActionButton" then
          set target to e
          exit repeat
        end if
      end try
    end repeat
    if target is missing value then return "NOTFOUND"
    set pos to position of target
    set sz to size of target
    set cx to (item 1 of pos) + ((item 1 of sz) / 2)
    set cy to (item 2 of pos) + ((item 2 of sz) / 2)
    click at {cx, cy}
    return "CLICKED at " & (cx as integer) & "," & (cy as integer) & "  (size " & (item 1 of sz as integer) & "x" & (item 2 of sz as integer) & ")"
  end tell
end tell
'''


def main():
    ip, org = ip_org()
    print(f"BEFORE: IP={ip} | org={org} | {verdict(org)}")
    try:
        input("\nEnter to open the NordVPN menu and REAL-click the button "
              "(don't touch mouse/keyboard)... ")
    except (EOFError, KeyboardInterrupt):
        print("\nbye")
        return

    out, err = osa(CLICK_SCRIPT)
    print("osascript ->", out or ("ERR: " + err))

    time.sleep(4)
    ip, org = ip_org()
    print(f"\nAFTER:  IP={ip} | org={org}")
    print("VERDICT:", verdict(org))


if __name__ == "__main__":
    main()
