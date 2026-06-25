"""
ChromeDriver Auto-Updater
==========================
Ensures the local ChromeDriver binary always matches the installed Chrome version.

Uses Google's official Chrome for Testing (CfT) JSON endpoints to find the exact
matching driver for the currently installed Chrome, downloads it, and caches it
locally in a `drivers/` folder next to this script.

Usage:
    from chromedriver_updater import get_chromedriver_path
    service = Service(get_chromedriver_path())

The module will:
    1. Detect the installed Chrome version (e.g. 146.0.7680.178)
    2. Check if a cached driver already matches that *exact* version
    3. If not, download the correct driver from the CfT endpoint
    4. Return the path to the working chromedriver binary
"""

import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).parent
DRIVERS_DIR = BASE_DIR / "drivers"
DRIVERS_DIR.mkdir(exist_ok=True)

# Version cache — stores the version of the currently cached driver
VERSION_FILE = DRIVERS_DIR / "chromedriver_version.txt"

# Google CfT endpoints
CFT_KNOWN_GOOD = "https://googlechromelabs.github.io/chrome-for-testing/known-good-versions-with-downloads.json"
CFT_LAST_KNOWN_GOOD = "https://googlechromelabs.github.io/chrome-for-testing/last-known-good-versions-with-downloads.json"

# Chrome binary paths per OS
CHROME_PATHS = {
    "darwin": [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ],
    "linux": [
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/usr/bin/chromium-browser",
        "/usr/bin/chromium",
    ],
    "win32": [
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
    ],
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_platform_key():
    """Return the CfT platform key (e.g. 'mac-arm64', 'mac-x64', 'linux64', 'win64')."""
    system = platform.system().lower()
    machine = platform.machine().lower()

    if system == "darwin":
        return "mac-arm64" if machine == "arm64" else "mac-x64"
    elif system == "linux":
        return "linux64"
    elif system == "windows":
        return "win64" if "64" in machine else "win32"
    raise RuntimeError(f"Unsupported platform: {system} {machine}")


def _get_chrome_version() -> str:
    """Detect the installed Chrome version string (e.g. '146.0.7680.178')."""
    plat = sys.platform
    if plat not in CHROME_PATHS:
        plat = "linux"  # fallback

    for chrome_path in CHROME_PATHS.get(plat, []):
        if os.path.exists(chrome_path):
            try:
                result = subprocess.run(
                    [chrome_path, "--version"],
                    capture_output=True, text=True, timeout=10,
                )
                match = re.search(r'(\d+\.\d+\.\d+\.\d+)', result.stdout)
                if match:
                    return match.group(1)
            except Exception:
                continue

    raise RuntimeError(
        "Could not detect Chrome version. Is Chrome installed?"
    )


def _get_cached_version() -> str | None:
    """Read the version of the currently cached chromedriver."""
    if VERSION_FILE.exists():
        return VERSION_FILE.read_text().strip()
    return None


def _save_cached_version(version: str):
    VERSION_FILE.write_text(version)


def _chromedriver_binary_name():
    return "chromedriver.exe" if sys.platform == "win32" else "chromedriver"


def _cached_driver_path() -> Path:
    return DRIVERS_DIR / _chromedriver_binary_name()


def _download_driver(url: str, dest: Path):
    """Download and extract the chromedriver binary from a zip URL."""
    print(f"   ⬇️  Downloading ChromeDriver from {url}")
    with tempfile.TemporaryDirectory() as tmpdir:
        zip_path = os.path.join(tmpdir, "chromedriver.zip")

        # Download
        urllib.request.urlretrieve(url, zip_path)

        # Extract
        with zipfile.ZipFile(zip_path, 'r') as zf:
            zf.extractall(tmpdir)

        # Find the chromedriver binary inside the extracted directory
        binary_name = _chromedriver_binary_name()
        found = None
        for root, dirs, files in os.walk(tmpdir):
            if binary_name in files:
                found = os.path.join(root, binary_name)
                break

        if not found:
            raise RuntimeError(f"chromedriver binary not found in downloaded archive from {url}")

        # Move to final location
        shutil.copy2(found, dest)
        # Make executable
        dest.chmod(dest.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

        # Clear macOS Gatekeeper attributes that block execution (exit code -9)
        if sys.platform == "darwin":
            try:
                subprocess.run(["xattr", "-c", str(dest)], capture_output=True, timeout=5)
            except Exception:
                pass

    print(f"   ✅ ChromeDriver installed at {dest}")


def _find_exact_version_url(chrome_version: str, platform_key: str) -> str | None:
    """Try to find an exact version match from the CfT known-good-versions endpoint."""
    try:
        print(f"   🔍 Looking for exact ChromeDriver match for Chrome {chrome_version}...")
        req = urllib.request.urlopen(CFT_KNOWN_GOOD, timeout=15)
        data = json.loads(req.read())

        for entry in data.get("versions", []):
            if entry["version"] == chrome_version:
                downloads = entry.get("downloads", {}).get("chromedriver", [])
                for dl in downloads:
                    if dl["platform"] == platform_key:
                        return dl["url"]
        return None
    except Exception as e:
        print(f"   ⚠️ Failed to query exact versions: {e}")
        return None


def _find_closest_version_url(chrome_version: str, platform_key: str) -> tuple[str | None, str | None]:
    """Fallback: find a driver matching the closest version (matching MAJOR.MINOR.BUILD)."""
    try:
        parts = chrome_version.split(".")
        if len(parts) >= 3:
            prefix = f"{parts[0]}.{parts[1]}.{parts[2]}."
        else:
            prefix = f"{parts[0]}."

        print(f"   🔍 Falling back to closest match for prefix {prefix}...")
        req = urllib.request.urlopen(CFT_KNOWN_GOOD, timeout=15)
        data = json.loads(req.read())

        closest_version = None
        closest_url = None

        for entry in data.get("versions", []):
            ver = entry.get("version", "")
            if ver.startswith(prefix):
                downloads = entry.get("downloads", {}).get("chromedriver", [])
                for dl in downloads:
                    if dl["platform"] == platform_key:
                        closest_version = ver
                        closest_url = dl["url"]
        
        if closest_url:
            print(f"   ℹ️  Best available match: ChromeDriver {closest_version}")
            return closest_url, closest_version
        
        return None, None
    except Exception as e:
        print(f"   ⚠️ Failed to query closest matching version: {e}")
        return None, None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_chromedriver_path() -> str:
    """
    Returns the path to a ChromeDriver binary that matches the installed Chrome.

    Automatically downloads or updates the driver if needed.
    This replaces ChromeDriverManager().install() with a more reliable approach.
    """
    chrome_version = _get_chrome_version()
    cached_version = _get_cached_version()
    driver_path = _cached_driver_path()

    print(f"   🌐 Installed Chrome: {chrome_version}")

    # Check if cached driver matches
    if cached_version == chrome_version and driver_path.exists():
        print(f"   ✅ Cached ChromeDriver matches ({cached_version})")
        return str(driver_path)

    if cached_version:
        print(f"   🔄 Version mismatch: cached={cached_version}, chrome={chrome_version}")
    else:
        print(f"   📥 No cached ChromeDriver found")

    platform_key = _get_platform_key()

    # Strategy 1: Exact version match
    url = _find_exact_version_url(chrome_version, platform_key)
    if url:
        _download_driver(url, driver_path)
        _save_cached_version(chrome_version)
        return str(driver_path)

    # Strategy 2: Closest version match (matching MAJOR.MINOR.BUILD)
    url, fallback_version = _find_closest_version_url(chrome_version, platform_key)
    if url:
        _download_driver(url, driver_path)
        # Store the actual Chrome version so next time it re-checks if Chrome updates
        _save_cached_version(chrome_version)
        return str(driver_path)

    # Strategy 3: Fall back to webdriver_manager as last resort
    print("   ⚠️ CfT endpoints failed, falling back to webdriver_manager...")
    try:
        from webdriver_manager.chrome import ChromeDriverManager
        path = ChromeDriverManager().install()
        return path
    except Exception as e:
        raise RuntimeError(
            f"All ChromeDriver download strategies failed. Chrome={chrome_version}, Error={e}"
        )


# ---------------------------------------------------------------------------
# Chrome profile cache cleanup
# ---------------------------------------------------------------------------

# Transient cache directories that Chrome rebuilds on demand.
# Removing these before launch prevents "Chrome instance exited" / "tab crashed"
# failures caused by corrupted caches or runaway disk usage. Login data,
# cookies, preferences, and bookmarks are NOT in this list and are preserved.
_TRANSIENT_CACHE_DIRS = (
    "Cache",
    "Code Cache",
    "GPUCache",
    "DawnGraphiteCache",
    "DawnWebGPUCache",
    "Service Worker",
    "AutofillAiModelCache",
    "optimization_guide_hint_cache_store",
    "optimization_guide_model_store",
    "component_crx_cache",
    "extensions_crx_cache",
    "ShaderCache",
    "GrShaderCache",
    "Crashpad",
)


def clear_chrome_caches(profile_dir):
    """Delete transient Chrome cache directories to keep the profile lean and stable.

    Safe to call before every driver launch — only cache/crash artifacts are removed.
    Login state (Cookies, Login Data, Local Storage) is preserved.
    """
    profile_path = Path(profile_dir)
    if not profile_path.exists():
        return

    removed = []
    # Caches can live either at profile root (e.g. Crashpad, component_crx_cache)
    # or inside the Default profile subdir (most user caches).
    candidates = [profile_path]
    default_dir = profile_path / "Default"
    if default_dir.exists():
        candidates.append(default_dir)

    for base in candidates:
        for name in _TRANSIENT_CACHE_DIRS:
            target = base / name
            if target.exists():
                try:
                    shutil.rmtree(target, ignore_errors=True)
                    removed.append(target.name)
                except Exception:
                    pass

    if removed:
        print(f"   🧹 Cleared Chrome caches: {', '.join(sorted(set(removed)))}")


# ---------------------------------------------------------------------------
# CLI: run directly to test/update
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    try:
        path = get_chromedriver_path()
        print(f"\n✅ ChromeDriver ready: {path}")

        # Quick smoke test
        result = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=5)
        print(f"   Version: {result.stdout.strip()}")
    except Exception as e:
        print(f"\n❌ Failed: {e}")
        sys.exit(1)
