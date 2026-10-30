#!/usr/bin/env python3
"""
ElevenLabs TTS – txt fájlokból mp3 generálás, böngésző-automatizálással.

Használat:
    python3 elevenlabs_tts.py <txt1> [<txt2> ...]

Minden txt mellé ugyanolyan nevű mp3 kerül (pl. tts_idojaras.txt → tts_idojaras.mp3).
Ha az mp3 már létezik, kihagyja. Minden 2 chunkhoz új Chrome-session (anonymous limit).
"""

import base64
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

TTS_URL = "https://elevenlabs.io/text-to-speech/hungarian"


class RateLimitError(Exception):
    pass

class _RetrySession(Exception):
    pass
CHROME_BINARY = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
CHROME_DEFAULT = Path.home() / "Library/Application Support/Google/Chrome/Default"

# We launch with --disable-extensions, so copying the (multi-GB) Extensions dir
# and Chrome caches into the temp profile is pure waste — and it fills the disk
# (Errno 28: No space left on device). Only cookies/auth state are needed.
_PROFILE_IGNORE = shutil.ignore_patterns(
    "Extensions", "Extension State", "Extension Rules", "Extension Scripts",
    "Cache", "Code Cache", "GPUCache", "DawnCache", "DawnGraphiteCache",
    "DawnWebGPUCache", "GrShaderCache", "ShaderCache", "Service Worker",
    "component_crx_cache", "extensions_crx_cache",
)
MAX_CHARS = 1000
MAX_WAIT_SECONDS = 90
CHUNKS_PER_SESSION = 2
VPN_RECONNECT_RETRIES = 3


def _vpn_reconnect():
    """Rotate IP via NordVPN URL scheme (no UI popup)."""
    print("  🔄 VPN reconnect (IP rotáció)...")
    ip_before = subprocess.run(
        ["curl", "-s", "--max-time", "5", "https://ipinfo.io/ip"],
        capture_output=True, text=True,
    ).stdout.strip()
    subprocess.run(["open", "nordvpn://disconnect"], check=True)
    time.sleep(4)
    subprocess.run(["open", "nordvpn://connect"], check=True)
    for _ in range(15):
        time.sleep(2)
        ip_after = subprocess.run(
            ["curl", "-s", "--max-time", "5", "https://ipinfo.io/ip"],
            capture_output=True, text=True,
        ).stdout.strip()
        if ip_after and ip_after != ip_before:
            print(f"  ✅ Új IP: {ip_after}")
            return
    print(f"  ⚠ IP nem változott ({ip_before}), folytatás...")


def _split_long_line(line: str, max_chars: int) -> list[str]:
    parts: list[str] = []
    while len(line) > max_chars:
        cut = line.rfind(". ", 0, max_chars)
        if cut == -1:
            cut = line.rfind(" ", 0, max_chars)
        if cut == -1:
            cut = max_chars
        else:
            cut += 1
        parts.append(line[:cut].rstrip())
        line = line[cut:].lstrip()
    if line:
        parts.append(line)
    return parts


def split_into_chunks(text: str, max_chars: int = MAX_CHARS) -> list[str]:
    raw_lines: list[str] = []
    for line in text.splitlines():
        if len(line) > max_chars:
            raw_lines.extend(_split_long_line(line, max_chars))
        else:
            raw_lines.append(line)

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for line in raw_lines:
        line_len = len(line) + 1
        if current and current_len + line_len > max_chars:
            chunks.append("\n".join(current))
            current = [line]
            current_len = line_len
        else:
            current.append(line)
            current_len += line_len
    if current:
        chunks.append("\n".join(current))
    return chunks


_SILENCE_CACHE: bytes | None = None


def _silence_mp3(seconds: float = 0.5) -> bytes:
    """Return a cached ~0.5s silent MP3 (ElevenLabs format) for gap insertion.

    Generated once per process via ffmpeg. Returns b"" on any failure so
    concatenation never crashes when ffmpeg is missing/errors.
    """
    global _SILENCE_CACHE
    if _SILENCE_CACHE is not None:
        return _SILENCE_CACHE
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tf:
            tmp = tf.name
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi",
             "-i", "anullsrc=channel_layout=mono:sample_rate=44100",
             "-t", str(seconds), "-c:a", "libmp3lame", "-b:a", "128k",
             "-f", "mp3", tmp],
            check=True, capture_output=True,
        )
        _SILENCE_CACHE = Path(tmp).read_bytes()
        Path(tmp).unlink(missing_ok=True)
    except Exception as e:
        print(f"  ⚠ Silence MP3 generálás sikertelen ({e}), gap nélkül fűzés.")
        _SILENCE_CACHE = b""
    return _SILENCE_CACHE


def decode_audio(raw: bytes) -> bytes:
    if not raw or raw[0] not in (ord("{"), ord("[")):
        return raw
    decoded = b""
    for line in raw.decode("utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            chunk = json.loads(line)
            if chunk.get("audio_base64"):
                decoded += base64.b64decode(chunk["audio_base64"])
        except json.JSONDecodeError:
            pass
    return decoded if decoded else raw


def generate_file(txt_path: Path, mp3_path: Path) -> None:
    full_text = txt_path.read_text(encoding="utf-8").strip()
    chunks = split_into_chunks(full_text)
    total_sessions = (len(chunks) + CHUNKS_PER_SESSION - 1) // CHUNKS_PER_SESSION
    print(f"  {len(full_text)} kar → {len(chunks)} chunk, {total_sessions} session")

    audio_parts: list[bytes] = []
    vpn_retries_left = VPN_RECONNECT_RETRIES

    for session_start in range(0, len(chunks), CHUNKS_PER_SESSION):
        session_chunks = chunks[session_start:session_start + CHUNKS_PER_SESSION]
        session_num = session_start // CHUNKS_PER_SESSION + 1

        while True:  # retry loop for VPN reconnect
          tmp_dir = tempfile.mkdtemp(prefix="elevenlabs_chrome_")
          try:
            print(f"  Session {session_num}/{total_sessions} "
                  f"(chunk {session_start+1}–{session_start+len(session_chunks)})")
            shutil.copytree(str(CHROME_DEFAULT), str(Path(tmp_dir) / "Default"), ignore=_PROFILE_IGNORE)

            with sync_playwright() as p:
                context = p.chromium.launch_persistent_context(
                    user_data_dir=tmp_dir,
                    executable_path=CHROME_BINARY,
                    headless=False,
                    args=["--disable-blink-features=AutomationControlled", "--disable-extensions"],
                    no_viewport=True,
                )
                page = context.new_page()
                captured: dict = {}

                def on_response(resp):
                    if "api.elevenlabs.io" in resp.url and "text-to-speech" in resp.url:
                        print(f"    TTS response: {resp.status}")
                        if resp.status == 200:
                            try:
                                captured["audio"] = resp.body()
                                print(f"    Audio: {len(captured['audio']):,} byte")
                            except Exception as e:
                                print(f"    body() hiba: {e}")
                        else:
                            captured["error"] = resp.status
                            try:
                                print(f"    Hiba: {resp.json()}")
                            except Exception:
                                pass

                page.on("response", on_response)

                for i, chunk_text in enumerate(session_chunks):
                    global_i = session_start + i
                    print(f"    [{global_i+1}/{len(chunks)}] {len(chunk_text)} kar: "
                          f"{chunk_text[:50].replace(chr(10), ' ')}...")

                    captured.clear()
                    page.goto(TTS_URL, wait_until="networkidle", timeout=30000)
                    page.wait_for_timeout(2000)

                    try:
                        page.click("button:has-text('Accept all cookies')", timeout=2000)
                        page.wait_for_timeout(500)
                    except PlaywrightTimeout:
                        pass

                    textarea = page.wait_for_selector("textarea", timeout=10000)
                    textarea.click()
                    textarea.fill(chunk_text)
                    page.wait_for_timeout(1000)

                    page.wait_for_selector("button:has-text('Play')", timeout=10000).click()
                    print(f"    Play → várakozás (max {MAX_WAIT_SECONDS}s)...")

                    limit_hit = False
                    for j in range(MAX_WAIT_SECONDS):
                        page.wait_for_timeout(1000)
                        if j % 15 == 14:
                            print(f"      ... {j+1}s")
                        if "audio" in captured:
                            break
                        # Limit popup felismerése — ne várjunk 90s-t feleslegesen
                        limit_texts = [
                            "reached the limit",
                            "logged-out user",
                            "sign up to continue",
                            "create a free account",
                            "generation limit",
                        ]
                        try:
                            page_text = page.locator("body").inner_text(timeout=500).lower()
                            if any(t in page_text for t in limit_texts):
                                print(f"    ⚠ Limit popup észlelve ({j+1}s után)")
                                limit_hit = True
                                break
                        except Exception:
                            pass

                    if "audio" not in captured:
                        context.close()
                        if limit_hit:
                            if vpn_retries_left > 0:
                                vpn_retries_left -= 1
                                print(f"  ⚠ Limit elérve, VPN reconnect ({vpn_retries_left} retry maradt)...")
                                _vpn_reconnect()
                                # Retry this entire session from session_start
                                raise _RetrySession()
                            raise RateLimitError(
                                f"IP limit elérve a {global_i+1}. chunknál, VPN retry kimerült."
                            )
                        status = captured.get("error", "nincs válasz")
                        raise RuntimeError(
                            f"Nem érkezett audio a {global_i+1}. chunknál "
                            f"(status: {status})."
                        )

                    audio_parts.append(decode_audio(captured["audio"]))

                context.close()
          except _RetrySession:
              pass  # while True loops back → new Chrome session, same session_start
          else:
              break  # session completed successfully → advance to next session_start
          finally:
              shutil.rmtree(tmp_dir, ignore_errors=True)

    silence = _silence_mp3(0.5) if len(audio_parts) > 1 else b""
    if silence:
        mp3_path.write_bytes(silence.join(audio_parts))
    else:
        mp3_path.write_bytes(b"".join(audio_parts))
    size = sum(len(p) for p in audio_parts)
    print(f"  → {mp3_path.name} ({size:,} byte, {len(audio_parts)} rész)")


def main(txt_paths: list[str]) -> None:
    if not txt_paths:
        sys.exit("Használat: elevenlabs_tts.py <txt1> [<txt2> ...]")

    jobs: list[tuple[Path, Path]] = []
    for t in txt_paths:
        txt = Path(t)
        if not txt.exists():
            print(f"⚠ Kihagyva (nem létezik): {txt}")
            continue
        mp3 = txt.with_suffix(".mp3")
        if mp3.exists():
            print(f"⏩ Kihagyva (mp3 már kész): {mp3.name}")
            continue
        jobs.append((txt, mp3))

    if not jobs:
        print("Nincs elvégzendő feladat.")
        return

    failed = []
    for txt, mp3 in jobs:
        print(f"\n{'='*50}")
        print(f"▶ {txt.name}")
        try:
            generate_file(txt, mp3)
        except RateLimitError as e:
            print(f"🚫 LIMIT: {e}")
            failed.append(txt.name)
        except Exception as e:
            print(f"❌ {txt.name} sikertelen: {e}")
            failed.append(txt.name)

    print(f"\n{'='*50}")
    if failed:
        print(f"⚠ Sikertelen: {', '.join(failed)}")
        sys.exit(1)
    else:
        print("✅ Minden TTS kész.")


if __name__ == "__main__":
    main(sys.argv[1:])
