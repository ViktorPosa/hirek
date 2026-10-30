#!/usr/bin/env python3
"""Diagnostic: what does the NordVPN AX tree look like from THIS (background)
execution context? Prints menu-bar/window counts and element dumps, with and
without activating the app."""

import subprocess


def osa(script, timeout=40):
    r = subprocess.run(["osascript", "-e", script],
                       capture_output=True, text=True, timeout=timeout)
    return (r.stdout or "").strip(), (r.stderr or "").strip()


DIAG = r'''
tell application "System Events"
  tell process "NordVPN"
    set info to ""
    try
      set info to info & "frontmost=" & (frontmost as text) & linefeed
    end try
    try
      set info to info & "menu bars=" & (count of menu bars) & linefeed
    end try
    try
      set info to info & "mb2 items=" & (count of menu bar items of menu bar 2) & linefeed
    end try
    try
      set mbi to menu bar item 1 of menu bar 2
      click mbi
    end try
    delay 2
    try
      set info to info & "windows after click=" & (count of windows) & linefeed
    end try
    set idx to 0
    repeat with win in (every window)
      set idx to idx + 1
      set info to info & "== window " & idx & " ==" & linefeed
      try
        repeat with e in (entire contents of win)
          set ln to "-"
          try
            set ln to ln & " role=" & (role of e)
          end try
          try
            set ln to ln & " id=" & (value of attribute "AXIdentifier" of e)
          end try
          try
            set ln to ln & " help=" & (help of e)
          end try
          set info to info & ln & linefeed
        end repeat
      end try
    end repeat
    return info
  end tell
end tell
'''

ACTIVATE_DIAG = 'tell application "NordVPN" to activate\ndelay 1\n' + DIAG


def main():
    print("===== WITHOUT activate =====", flush=True)
    out, err = osa(DIAG)
    print(out or ("ERR: " + err), flush=True)
    print("\n===== WITH activate =====", flush=True)
    out, err = osa(ACTIVATE_DIAG)
    print(out or ("ERR: " + err), flush=True)


if __name__ == "__main__":
    main()
