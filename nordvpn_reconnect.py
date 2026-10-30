#!/usr/bin/env python3
"""
NordVPN reconnect via AppleScript GUI automation.

Actions:
  status     — prints current VPN status (Connected / Disconnected)
  disconnect — disconnects VPN
  connect    — connects VPN (quick connect)
  reconnect  — disconnects then reconnects (IP change)

Usage:
    python3 nordvpn_reconnect.py [status|disconnect|connect|reconnect]
    Default: reconnect
"""

import subprocess
import sys
import time

_OPEN_AND_FIND = """
tell application "System Events"
    tell process "NordVPN"
        set mbi to menu bar item 1 of menu bar 2
        click mbi
        delay 2
        set allUI to {{}}
        repeat with win in (every window)
            try
                set allUI to allUI & (entire contents of win)
            end try
        end repeat
        {action}
    end tell
end tell
"""


def _run_as(script: str) -> str:
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True, text=True, timeout=20,
    )
    return result.stdout.strip()


def _get_status() -> str:
    script = _OPEN_AND_FIND.format(action="""
        repeat with e in allUI
            try
                set bid to value of attribute "AXIdentifier" of e
                if bid is "dashboard" then
                    -- close popup before returning
                    key code 53
                    return help of e
                end if
            end try
        end repeat
        key code 53
        return "unknown"
    """)
    raw = _run_as(script)
    # "VPN. Connected" or "VPN. Not connected"
    if "Connected" in raw and "Not" not in raw:
        return "Connected"
    elif "Not" in raw or "Disconnected" in raw:
        return "Disconnected"
    return raw


def _click_action_button() -> str:
    """Actuate the connectionCardActionButton (connect/disconnect toggle).

    NordVPN 10.8.x ignores a synthetic AXPress on this menu-bar popover button,
    so a plain `click e` opens the popup but never toggles the tunnel. We reuse
    the SAME popup-opening + element-find logic (which reliably opens the menu),
    then issue a real hardware-style `click at {x, y}` at the button's centre.
    No Escape afterwards (that used to dismiss it before the click registered).
    """
    script = _OPEN_AND_FIND.format(action="""
        repeat with e in allUI
            try
                set bid to value of attribute "AXIdentifier" of e
                if bid is "connectionCardActionButton" then
                    set pos to position of e
                    set sz to size of e
                    set cx to (item 1 of pos) + ((item 1 of sz) / 2)
                    set cy to (item 2 of pos) + ((item 2 of sz) / 2)
                    click at {cx, cy}
                    delay 1
                    return "clicked at " & (cx as integer) & "," & (cy as integer)
                end if
            end try
        end repeat
        key code 53
        return "not found"
    """)
    return _run_as(script)


def _close_popup():
    subprocess.run(["osascript", "-e", 'tell application "System Events" to key code 53'],
                   capture_output=True, timeout=5)


def cmd_status():
    status = _get_status()
    print(f"NordVPN: {status}")
    return status


def cmd_disconnect():
    # Single menu-open only: pre-checking status here used to open+close the
    # popup and the immediate reopen raced -> "button not found".
    print("NordVPN: disconnecting...")
    result = _click_action_button()
    print(f"NordVPN: action -> {result}")
    if not result.startswith("clicked"):
        raise RuntimeError(f"Could not find/actuate action button (result: {result})")
    time.sleep(3)
    status = _get_status()
    print(f"NordVPN: {status}")
    if status != "Disconnected":
        raise RuntimeError(f"Disconnect not confirmed, status: {status}")


def cmd_connect():
    status = _get_status()
    if status == "Connected":
        print("NordVPN: already connected")
        return
    print("NordVPN: connecting...")
    result = _click_action_button()
    if not result.startswith("clicked"):
        raise RuntimeError(f"Could not find action button (result: {result})")
    # Wait up to 20s for connection
    for i in range(20):
        time.sleep(2)
        status = _get_status()
        if status == "Connected":
            print(f"NordVPN: Connected ({(i+1)*2}s)")
            return
        if i % 5 == 4:
            print(f"  ... waiting {(i+1)*2}s")
    raise RuntimeError(f"Connect timed out, status: {status}")


def cmd_reconnect():
    """Rotate IP by disconnecting then reconnecting to a different server."""
    print("NordVPN: reconnecting (IP rotation)...")
    ip_before = subprocess.run(
        ["curl", "-s", "--max-time", "5", "https://ipinfo.io/ip"],
        capture_output=True, text=True
    ).stdout.strip()
    print(f"NordVPN: current IP = {ip_before}")

    # Click disconnect/connect button — if connected, this disconnects;
    # if already disconnected, this connects. We want the cycle.
    status = _get_status()
    if status == "Connected":
        print("NordVPN: clicking disconnect...")
        _click_action_button()
        time.sleep(5)  # wait for disconnect to settle
        # Now click connect to get a fresh server
        print("NordVPN: clicking connect (new server)...")
        _click_action_button()
    else:
        print("NordVPN: was disconnected, connecting...")
        _click_action_button()

    # Wait for connection
    for i in range(20):
        time.sleep(2)
        status = _get_status()
        if status == "Connected":
            ip_after = subprocess.run(
                ["curl", "-s", "--max-time", "5", "https://ipinfo.io/ip"],
                capture_output=True, text=True
            ).stdout.strip()
            print(f"NordVPN: Connected — new IP = {ip_after}")
            if ip_after == ip_before:
                print("NordVPN: ⚠ IP did not change")
            else:
                print("NordVPN: ✅ IP changed successfully")
            return
        if i % 5 == 4:
            print(f"  ... waiting {(i+1)*2}s")
    raise RuntimeError("Reconnect timed out")


def main():
    action = sys.argv[1] if len(sys.argv) > 1 else "reconnect"
    actions = {
        "status": cmd_status,
        "disconnect": cmd_disconnect,
        "connect": cmd_connect,
        "reconnect": cmd_reconnect,
    }
    if action not in actions:
        sys.exit(f"Unknown action: {action}. Use: {', '.join(actions)}")
    actions[action]()


if __name__ == "__main__":
    main()
