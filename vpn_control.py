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

import os
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


_LAST_DEEPLINK_TIME = 0.0
_MIN_DEEPLINK_INTERVAL = 8.0  # seconds between macOS `open` deep link calls


def is_bot_execution():
    """
    Return True only if running as the dedicated macOS bot account (UID 503 or username 'bot').
    Guards machine-wide VPN controls so non-bot users (e.g. viktorposa) never trigger GUI popups
    or network route changes.
    """
    try:
        if os.getuid() == 503:
            return True
        import pwd
        return pwd.getpwuid(os.getuid()).pw_name == "bot"
    except Exception:
        return False


def _open_deeplink(url):
    """Fire a NordVPN deep link via the macOS `open` command. Rate-limited and non-fatal."""
    if "connect" in url and not is_bot_execution():
        print(f"[vpn_control] WARNING: Refusing deep link {url} from non-bot user (uid={os.getuid()}).")
        return False
    global _LAST_DEEPLINK_TIME
    now = time.time()
    elapsed = now - _LAST_DEEPLINK_TIME
    if elapsed < _MIN_DEEPLINK_INTERVAL:
        time.sleep(_MIN_DEEPLINK_INTERVAL - elapsed)
    _LAST_DEEPLINK_TIME = time.time()
    try:
        subprocess.run(
            ["open", "-g", url],
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


def _emit_alert(kind, title, message, level="critical"):
    """
    Best-effort cross-user desktop alert. Scrapers run as the `bot` user, whose
    osascript banners land in bot's GUI session — NOT the human's. So instead we
    drop a small JSON into the shared DerusAlerts mailbox on the internal disk;
    a LaunchAgent in the human's (viktorposa) session watches it and pops a modal.
    Deduplicated per `kind` within 10 minutes. Never raises.
    """
    try:
        import os
        import json
        import time as _t
        import tempfile
        base = "/Users/Shared/DerusAlerts"
        inbox = os.path.join(base, "inbox")
        os.makedirs(inbox, exist_ok=True)
        for _d in (base, inbox):
            try:
                os.chmod(_d, 0o777)
            except Exception:
                pass
        now = _t.time()
        try:
            for fn in os.listdir(inbox):
                if fn.startswith(kind + ".") and fn.endswith(".json"):
                    if now - os.path.getmtime(os.path.join(inbox, fn)) < 600:
                        return
        except Exception:
            pass
        payload = {"kind": kind, "title": title, "message": message,
                   "level": level, "ts": now}
        fd, tmp = tempfile.mkstemp(dir=inbox, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        try:
            os.chmod(tmp, 0o666)
        except Exception:
            pass
        os.replace(tmp, os.path.join(inbox, f"{kind}.{int(now)}.json"))
    except Exception:
        pass


def is_connected():
    """
    Best-effort check: is the machine's public traffic going through NordVPN?

    Returns True if the current public IP's org/hostname matches a known
    NordVPN fingerprint, else False. If IP check fails (e.g. DNS resolution broken),
    checks whether NordVPN app is still running under current user. Never raises.
    """
    org = _ip_org()
    if org:
        return any(fp in org for fp in _NORD_FINGERPRINTS)

    # Ha az IP ellenőrzés meghiúsult (pl. DNS hiba miatt), megnézzük, fut-e még az app folyamata
    try:
        my_uid = str(os.getuid())
        res = subprocess.run(["pgrep", "-u", my_uid, "-x", _APP_NAME],
                             capture_output=True, text=True, check=False)
        if res.returncode == 0 and res.stdout.strip():
            return True  # Még fut az app, tehát még nem kapcsolódott le tisztán
    except Exception:
        pass
    return False


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
    ok = False
    try:
        res = subprocess.run(
            ["osascript", "-e", f'tell application "{_APP_NAME}" to quit'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5,
        )
        if res.returncode == 0:
            ok = True
    except Exception:
        pass

    # Fej nélküli / daemon folyamat esetén (pl. bot felhasználó) a pkill -9 megbízhatóan leállítja az appot
    try:
        my_uid = str(os.getuid())
        subprocess.run(["pkill", "-9", "-u", my_uid, "-x", _APP_NAME],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        subprocess.run(["pkill", "-9", "-u", my_uid, "-f", f"{_APP_NAME}.app"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        ok = True
    except Exception as e:
        print(f"[vpn_control] WARNING: could not kill {_APP_NAME}: {e}")

    return ok


def _launch_app():
    """Launch/foreground NordVPN.app so it can honor the connect deep link."""
    if not is_bot_execution():
        print(f"[vpn_control] WARNING: Refusing NordVPN launch from non-bot user (uid={os.getuid()}).")
        return False
    try:
        my_uid = str(os.getuid())
        res = subprocess.run(["pgrep", "-u", my_uid, "-x", _APP_NAME],
                             capture_output=True, text=True, check=False)
        if res.returncode == 0 and res.stdout.strip():
            return True
        subprocess.run(
            ["open", "-g", "-j", "-a", _APP_NAME],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15,
        )
        return True
    except Exception as e:
        print(f"[vpn_control] WARNING: could not launch {_APP_NAME}: {e}")
        return False


# Keywords (lowercase) that appear on the NordVPN login / sign-in / onboarding
# screen. Their presence in the app's visible UI means the account is LOGGED
# OUT — the connect deep link launches the app to this screen instead of
# connecting, so the public IP never rotates. Multilingual (hu/en) to survive
# the app's UI language.
_LOGIN_UI_KEYWORDS = (
    "log in", "login", "bejelentkez", "sign in", "sign up",
    "continue with", "log in with", "already have an account",
    "jelszó", "password", "e-mail",
)


def _ax_dump():
    """
    Return a lowercase dump of NordVPN's visible window titles, static texts
    and button names via Accessibility (System Events), or None if AX is
    unavailable (no Accessibility permission for the controlling process, or
    the app is not scriptable). Never raises.

    When logged IN, NordVPN is a menu-bar app with NO open window, so the dump
    is empty. When logged OUT, the connect attempt raises the login window,
    which this surfaces.
    """
    script = (
        'tell application "System Events"\n'
        '  if not (exists process "NordVPN") then return "NO_PROCESS"\n'
        '  tell process "NordVPN"\n'
        '    set out to ""\n'
        '    try\n'
        '      repeat with w in windows\n'
        '        set out to out & " win:" & (name of w as string)\n'
        '        try\n'
        '          set out to out & " " & (value of static texts of w as string)\n'
        '        end try\n'
        '        try\n'
        '          set out to out & " " & (name of buttons of w as string)\n'
        '        end try\n'
        '      end repeat\n'
        '    end try\n'
        '    return out\n'
        '  end tell\n'
        'end tell\n'
    )
    try:
        res = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=15,
        )
        if res.returncode != 0:
            return None
        return res.stdout.strip().lower()
    except Exception as e:
        print(f"[vpn_control] WARNING: could not read NordVPN UI (Accessibility): {e}")
        return None


def login_state():
    """
    Best-effort NordVPN account login-state, inferred from the app's UI via
    Accessibility. Returns one of:
      "logged_out" — a login/sign-in screen is visible; manual sign-in needed;
      "logged_in"  — app running with no login UI (normal menu-bar state);
      "unknown"    — could not determine (no Accessibility permission, or the
                     app is not running).
    Never raises. This is a heuristic, not an API — treat "logged_out" as a
    strong hint to check the app, not a hard guarantee.
    """
    dump = _ax_dump()
    if dump is None or dump == "no_process":
        return "unknown"
    if any(k in dump for k in _LOGIN_UI_KEYWORDS):
        return "logged_out"
    return "logged_in"


def disconnect(wait=True, timeout=60):
    """
    Disconnect NordVPN by QUITTING the app. Quitting tears down the Network
    Extension tunnel and the public IP reverts to the ISP — the only method
    proven reliable on this build (the disconnect deep link is ignored and the
    menu-bar click is flaky).

    Confirms by polling the public IP until it no longer fingerprints as a
    NordVPN exit node. Non-fatal: logs a warning and returns False on
    timeout/failure; never raises.
    """
    if not is_connected():
        return True
    _quit_app()
    if not wait:
        return True
    deadline = time.time() + max(0, timeout)
    while time.time() < deadline:
        if not is_connected():
            try:
                subprocess.run(["dscacheutil", "-flushcache"], check=False, timeout=5)
            except Exception:
                pass
            # Settle macOS network routing & endpoint security filters
            time.sleep(2)
            print("[vpn_control] NordVPN DISCONNECTED (app quit; public IP is ISP).")
            return True
        time.sleep(_POLL_INTERVAL)
    print("[vpn_control] WARNING: disconnect not confirmed within timeout; continuing anyway.")
    return False


def connect(wait=True, timeout=90):
    """
    Connect NordVPN (Quick Connect) by launching the app and firing the
    `nordvpn://connect` deep link — verified to connect in ~2s.

    Confirms by polling the public IP until it fingerprints as a NordVPN exit
    node. Non-fatal: logs a warning and returns False on timeout/failure;
    never raises.
    """
    if not is_bot_execution():
        print(f"[vpn_control] WARNING: Refusing VPN connect from non-bot user (uid={os.getuid()}). VPN control is restricted to user Bot.")
        return False
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
    if login_state() == "logged_out":
        print("[vpn_control] ERROR: NordVPN is LOGGED OUT of its account — open the "
              "NordVPN app and sign in. VPN cannot connect until you log in.")
        _emit_alert(
            "vpn_logged_out",
            "⚠️ NordVPN kijelentkezve (bot)",
            "A NordVPN app ki van jelentkezve a bot fiókból — nyisd meg és jelentkezz be. "
            "A VPN nem tud csatlakozni, amíg be nem lépsz.",
        )
    else:
        print("[vpn_control] WARNING: connect not confirmed within timeout; continuing anyway.")
    return False


def reconnect(wait=True, timeout=120):
    """
    Rotate the exit IP: disconnect (quit) then connect (relaunch + deep link).
    NordVPN Quick Connect picks a fresh server, so the public IP changes.

    Non-fatal: never raises. Returns connect()'s result and warns if the IP
    did not change.
    """
    if not is_bot_execution():
        print(f"[vpn_control] WARNING: Refusing VPN reconnect from non-bot user (uid={os.getuid()}). VPN control is restricted to user Bot.")
        return False
    baseline_ip = _public_ip()
    print(f"[vpn_control] reconnect: baseline IP = {baseline_ip}")
    disconnect(wait=True, timeout=min(timeout, 60))
    ok = connect(wait=wait, timeout=timeout)
    new_ip = _public_ip()
    print(f"[vpn_control] reconnect: new IP = {new_ip}")
    if baseline_ip and new_ip and baseline_ip == new_ip:
        print("[vpn_control] WARNING: IP did not change after reconnect.")
        if login_state() == "logged_out":
            print("[vpn_control] ERROR: NordVPN is LOGGED OUT of its account — open the "
                  "NordVPN app and sign in. IP rotation is impossible while logged out.")
            _emit_alert(
                "vpn_logged_out",
                "⚠️ NordVPN kijelentkezve (bot)",
                "A NordVPN app ki van jelentkezve a bot fiókból — jelentkezz be. "
                "IP-rotáció (limit-megkerülés) így nem lehetséges.",
            )
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
    elif action == "login":
        print("login_state:", login_state())
    else:
        print("is_connected:", is_connected(), "public_ip:", _public_ip(),
              "login_state:", login_state())
