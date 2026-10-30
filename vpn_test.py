#!/usr/bin/env python3
"""
vpn_test.py
===========
Standalone VPN test harness. Wraps the pipeline's VPN helpers so you can
exercise connect / disconnect / reconnect / status by hand:

    python3 vpn_test.py status
    python3 vpn_test.py connect
    python3 vpn_test.py disconnect
    python3 vpn_test.py reconnect     # disconnect + reconnect (IP rotation)

Default action: status.

Uses vpn_control: connect = launch app + `nordvpn://connect` deep link,
disconnect = quit the app (tears down the tunnel), reconnect = both (IP
rotation). State is confirmed via the public IP, not the flaky app UI.
"""

import sys

import vpn_control


def do_status():
    print("is_connected (IP fingerprint):", vpn_control.is_connected())
    print("public_ip:", vpn_control._public_ip())


def do_connect():
    print("result:", vpn_control.connect(wait=True, timeout=90))


def do_disconnect():
    print("result:", vpn_control.disconnect(wait=True, timeout=60))


def do_reconnect():
    print("result:", vpn_control.reconnect(wait=True, timeout=120))


ACTIONS = {
    "status": do_status,
    "connect": do_connect,
    "disconnect": do_disconnect,
    "reconnect": do_reconnect,
}


def main():
    action = sys.argv[1] if len(sys.argv) > 1 else "status"
    if action not in ACTIONS:
        sys.exit(f"Unknown action: {action}. Use: {', '.join(ACTIONS)}")
    ACTIONS[action]()


if __name__ == "__main__":
    main()
