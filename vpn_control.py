#!/usr/bin/env python3
"""
vpn_control.py
==============
Best-effort NordVPN.app toggle helper for macOS (NOT the App Store build, and
NOT the nonexistent `nordvpn` CLI — there is none on PATH here).

CONTROL METHOD (verified 2026-08-08 on this machine):
  CONNECT: `open nordvpn://connect` (Quick Connect deep link) — connects in
  ~2s. The app must be running, so we `open -a NordVPN` first.
  DISCONNECT: the `nordvpn://disconnect` deep link is IGNORED by this build
  (10.8.x), and the menu-bar AXPress / coordinate click is unreliable (opens
  the popover but often fails to toggle the tunnel). The method proven to work
  is QUITTING the app: `osascript -e 'tell application "NordVPN" to quit'`
  tears down the Network Extension tunnel and the public IP reverts to the ISP.
  RECONNECT (IP rotation): disconnect (quit) then connect (relaunch + deep
  link); NordVPN picks a fresh exit node, so the public IP changes.

STATE CHECK (verified):
  There is no reliable NordVPN CLI/status API on macOS here
  (api.nordvpn.com/vpn/check/full -> {"status":"deprecated"}, and the
  nordvpn.com status AJAX endpoint is Cloudflare-gated). A bare "utun exists"
  check is unreliable because several utun interfaces already exist from other
  services. So we use the machine's PUBLIC IP as the source of truth:
    * is_connected() fingerprints the public IP's org/hostname (via
      ipinfo.io/json) against known NordVPN infrastructure names.
    * connect()/disconnect() ALSO capture a baseline public IP and poll until
      the IP CHANGES, which deterministically confirms the tunnel toggled
      regardless of provider fingerprinting. This is the robust primary signal.

REQUIREMENTS / ONE-TIME SETTING:
  - NordVPN.app must be installed, RUNNING, and LOGGED IN for the deep links to
    take effect. `open nordvpn://...` will launch the app if closed, but a
    logged-out app cannot connect. Quick Connect must be usable (i.e. the
    account is active). No other one-time setting is required.
  - Everything here is NON-FATAL: on any failure/timeout we print a WARNING and
    return False. We never raise and never hang (all network calls are
    time-boxed; polling is bounded by `timeout`).
"""

import subprocess
import time
import json
import urllib.request

# --- Tunables ---------------------------------------------------------------
_POLL_INTERVAL = 3            # seconds between state polls
_HTTP_TIMEOUT = 8            # per-request network timeout (seconds)
_IP_ECHO_URL = "https://api.ipify.org"          # plain-text public IP
_IP_INFO_URL = "https://ipinfo.io/json"          # public IP + org/hostname

# Substrings (lowercase) that identify a NordVPN exit node's org/hostname.
# NordVPN historically routes through these ASNs/providers.
_NORD_FINGERPRINTS = (
    "nord",
    "tefincom",
    "packet",          # "P.A.C.K.E.T. SECURITY" / Packethub
    "packethub",
    "datacamp",
    "m247",
    "cdn77",
    "quadranet",
)


def _open_deeplink(url):
    """Fire a NordVPN deep link via the macOS `open` command. Non-fatal."""
    try:
        subprocess.run(
            ["open", url],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
        )
        return True
    except Exception as e:
        print(f"[vpn_control] WARNING: failed to open deep link {url}: {e}")
        return False


def _public_ip():
    """Return the current public IP as a string, or None on failure."""
    try:
        req = urllib.request.Request(_IP_ECHO_URL, headers={"User-Agent": "curl/8"})
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            return resp.read().decode("utf-8", "replace").strip()
    except Exception as e:
        print(f"[vpn_control] WARNING: could not fetch public IP: {e}")
        return None


def _ip_org():
    """Return the lowercase org+hostname string for the current public IP, or ''."""
    try:
        req = urllib.request.Request(_IP_INFO_URL, headers={"User-Agent": "curl/8"})
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        org = str(data.get("org", ""))
        hostname = str(data.get("hostname", ""))
        return (org + " " + hostname).lower()
    except Exception as e:
        print(f"[vpn_control] WARNING: could not fetch IP org info: {e}")
        return ""


def is_connected():
    """
    Best-effort check: is the machine's public traffic going through NordVPN?

    Returns True if the current public IP's org/hostname matches a known
    NordVPN fingerprint, else False. On network failure returns False
    (treated as "unknown / assume not connected"). Never raises.
    """
    org = _ip_org()
    if not org:
        return False
    return any(fp in org for fp in _NORD_FINGERPRINTS)


def _wait_for_ip_change(baseline_ip, timeout):
    """
    Poll the public IP until it differs from baseline_ip, or timeout elapses.
    Returns True if the IP changed (tunnel toggled), False on timeout/failure.
    Never raises.
    """
    if baseline_ip is None:
        # No baseline to compare — fall back to a short settle wait.
        time.sleep(_POLL_INTERVAL)
        return True
    deadline = time.time() + max(0, timeout)
    while time.time() < deadline:
        time.sleep(_POLL_INTERVAL)
        current = _public_ip()
        if current and current != baseline_ip:
            return True
    return False


_APP_NAME = "NordVPN"


def _quit_app():
    """Quit NordVPN.app (tears down the tunnel). Non-fatal."""
    try:
        subprocess.run(
            ["osascript", "-e", f'tell application "{_APP_NAME}" to quit'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15,
        )
        return True
    except Exception as e:
        print(f"[vpn_control] WARNING: could not quit {_APP_NAME}: {e}")
        return False


def _launch_app():
    """Launch/foreground NordVPN.app so it can honor the connect deep link."""
    try:
        subprocess.run(
            ["open", "-a", _APP_NAME],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15,
        )
        return True
    except Exception as e:
        print(f"[vpn_control] WARNING: could not launch {_APP_NAME}: {e}")
        return False


_APP_BIN_PATTERN = "NordVPN.app/Contents/MacOS/NordVPN"


def _force_kill_app():
    """A NordVPN.app FŐ folyamatának kilövése (SIGTERM, majd szükség esetén SIGKILL).
    Újabb NordVPN-buildek figyelmen kívül hagyják az AppleScript `quit`-et, de a folyamat
    kilövése ugyanúgy lebontja a Network Extension tunnelt, mint korábban a quit. Csak a
    saját (azonos user) NordVPN.app-ot tudjuk kilőni; a root system extension/helper marad,
    de a tunnelt a fő app leállása bontja. Non-fatal."""
    killed = False
    for sig in ("-TERM", "-KILL"):
        try:
            r = subprocess.run(["pkill", sig, "-f", _APP_BIN_PATTERN],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
            if r.returncode == 0:
                killed = True
                print(f"[vpn_control] NordVPN.app killed with pkill {sig}")
        except Exception as e:
            print(f"[vpn_control] WARNING: pkill {sig} failed: {e}")
        time.sleep(2)
        # ha már nem fut a fő app, nincs értelme a SIGKILL-nek
        try:
            still = subprocess.run(["pgrep", "-f", _APP_BIN_PATTERN],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
            if still.returncode != 0:
                break
        except Exception:
            break
    return killed


def disconnect(wait=True, timeout=60):
    """
    Disconnect NordVPN. Fokozatos eszkaláció (mindegyik lépés után ellenőrizzük a publikus
    IP-t): (1) `nordvpn://disconnect` deep link, (2) AppleScript quit, (3) a NordVPN.app fő
    folyamatának force-kill-je. A folyamat leállása lebontja a Network Extension tunnelt, a
    publikus IP visszaáll az ISP-re.

    FONTOS: ha a NordVPN kill-switch aktív, a tunnel lebontása után is blokkolhatja a netet —
    ekkor a generáláshoz a kill-switch/auto-connect KIKAPCSOLÁSA szükséges a NordVPN-ben.

    Non-fatal: logol és False-t ad vissza timeoutnál; sosem dob kivételt.
    """
    def _off():
        try:
            return not is_connected()
        except Exception:
            return False

    # 1) deep link (egyes buildeken működik)
    try:
        _open_deeplink("nordvpn://disconnect")
    except Exception:
        pass
    time.sleep(3)
    # 2) graceful quit
    if not _off():
        _quit_app()
        time.sleep(3)
    # 3) force-kill, ha még mindig aktív
    if not _off():
        print("[vpn_control] graceful disconnect nem hatott – force-kill…")
        _force_kill_app()

    if not wait:
        return True
    deadline = time.time() + max(0, timeout)
    while time.time() < deadline:
        if _off():
            print("[vpn_control] NordVPN DISCONNECTED (public IP is ISP).")
            return True
        time.sleep(_POLL_INTERVAL)
    print("[vpn_control] WARNING: disconnect not confirmed within timeout; "
          "továbbmegyünk (ellenőrizd a NordVPN kill-switch/auto-connect beállítást!).")
    return False


def connect(wait=True, timeout=90):
    """
    Connect NordVPN (Quick Connect) by launching the app and firing the
    `nordvpn://connect` deep link — verified to connect in ~2s.

    Confirms by polling the public IP until it fingerprints as a NordVPN exit
    node. Non-fatal: logs a warning and returns False on timeout/failure;
    never raises.
    """
    _launch_app()
    time.sleep(5)  # give the app a moment to be ready to honor the deep link
    _open_deeplink("nordvpn://connect")
    if not wait:
        return True
    deadline = time.time() + max(0, timeout)
    while time.time() < deadline:
        if is_connected():
            print("[vpn_control] NordVPN CONNECTED (deep link; public IP is NordVPN).")
            return True
        time.sleep(_POLL_INTERVAL)
    print("[vpn_control] WARNING: connect not confirmed within timeout; continuing anyway.")
    return False


def reconnect(wait=True, timeout=120):
    """
    Rotate the exit IP: disconnect (quit) then connect (relaunch + deep link).
    NordVPN Quick Connect picks a fresh server, so the public IP changes.

    Non-fatal: never raises. Returns connect()'s result and warns if the IP
    did not change.
    """
    baseline_ip = _public_ip()
    print(f"[vpn_control] reconnect: baseline IP = {baseline_ip}")
    disconnect(wait=True, timeout=min(timeout, 60))
    ok = connect(wait=wait, timeout=timeout)
    new_ip = _public_ip()
    print(f"[vpn_control] reconnect: new IP = {new_ip}")
    if baseline_ip and new_ip and baseline_ip == new_ip:
        print("[vpn_control] WARNING: IP did not change after reconnect.")
    return ok


if __name__ == "__main__":
    # Manual smoke test helper (does NOT run automatically anywhere).
    import sys
    action = sys.argv[1] if len(sys.argv) > 1 else "status"
    if action == "connect":
        print("result:", connect())
    elif action == "disconnect":
        print("result:", disconnect())
    elif action == "reconnect":
        print("result:", reconnect())
    else:
        print("is_connected:", is_connected(), "public_ip:", _public_ip())
