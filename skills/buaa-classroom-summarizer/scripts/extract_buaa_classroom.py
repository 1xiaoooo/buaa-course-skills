#!/usr/bin/env python3
"""
Extract BUAA classroom replay metadata, playable video URLs, and course transcript text.

Example:
  python extract_buaa_classroom.py "https://classroom.msa.buaa.edu.cn/livingroom?course_id=136814&sub_id=5660610&tenant_code=21" --output-dir out
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import imageio_ffmpeg
import requests
from Cryptodome.Cipher import AES

try:
    import win32crypt  # type: ignore
except ImportError:
    win32crypt = None


SCRIPT_DIR = Path(__file__).resolve().parent
PPT_OUTLINE_SCRIPT = SCRIPT_DIR / "extract_ppt_outline.py"
SESSION_CACHE = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "cache" / "buaa_browser_session.json"
RUNTIME_BROWSER_PROFILE = (
    Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "cache" / "buaa_browser_runtime_profile"
)


def configure_utf8_stdio() -> None:
    for name in ("stdin", "stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def utf8_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


def current_platform() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def build_browser_configs() -> dict[str, dict[str, Any]]:
    home = Path.home()
    platform_name = current_platform()
    if platform_name == "windows":
        edge_root = home / "AppData" / "Local" / "Microsoft" / "Edge" / "User Data"
        chrome_root = home / "AppData" / "Local" / "Google" / "Chrome" / "User Data"
    elif platform_name == "macos":
        edge_root = home / "Library" / "Application Support" / "Microsoft Edge"
        chrome_root = home / "Library" / "Application Support" / "Google" / "Chrome"
    else:
        edge_root = home / ".config" / "microsoft-edge"
        chrome_root = home / ".config" / "google-chrome"
    return {
        "msedge": {
            "display_name": "Edge",
            "local_state": edge_root / "Local State",
            "cookie_db": edge_root / "Default" / "Network" / "Cookies",
            "playwright_channel": "msedge",
            "temp_cookie_copy": "msedge_buaa_cookie_copy.db",
        },
        "chrome": {
            "display_name": "Chrome",
            "local_state": chrome_root / "Local State",
            "cookie_db": chrome_root / "Default" / "Network" / "Cookies",
            "playwright_channel": "chrome",
            "temp_cookie_copy": "chrome_buaa_cookie_copy.db",
        },
    }


BROWSER_CONFIGS = build_browser_configs()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("url", help="BUAA livingroom URL")
    parser.add_argument("--output-dir", default="buaa_classroom_output", help="Directory to write extracted files")
    parser.add_argument(
        "--browser-runtime-auth",
        "--edge-runtime-auth",
        dest="browser_runtime_auth",
        action="store_true",
        help="Launch a dedicated Chromium window and read BUAA cookies from that runtime if live cookies/cache are unavailable",
    )
    parser.add_argument(
        "--browser-runtime-profile-dir",
        "--edge-runtime-profile-dir",
        dest="browser_runtime_profile_dir",
        default=str(RUNTIME_BROWSER_PROFILE),
        help="Persistent profile directory for the dedicated browser runtime-auth window",
    )
    parser.add_argument(
        "--browser-channel",
        choices=["auto", "msedge", "chrome"],
        default="auto",
        help="Chromium browser to use for runtime auth and local cookie access",
    )
    parser.add_argument(
        "--browser-login-timeout",
        "--edge-login-timeout",
        dest="browser_login_timeout",
        type=int,
        default=180,
        help="Seconds to wait for the user to complete login in the dedicated browser runtime-auth window",
    )
    parser.add_argument(
        "--preferred-stream",
        choices=["teacher", "ppt", "auto"],
        default="teacher",
        help="Preferred replay stream when both teacher and PPT videos exist",
    )
    parser.add_argument(
        "--extract-ppt-outline",
        action="store_true",
        help="Extract a page-level PPT outline from the PPT replay stream when available",
    )
    parser.add_argument(
        "--export-markdown-note",
        action="store_true",
        help="Export a standalone Markdown lesson note without relying on Obsidian",
    )
    parser.add_argument(
        "--markdown-note-file",
        default="",
        help="Optional Markdown note path. Defaults to <output-dir>/lesson_note.md",
    )
    parser.add_argument(
        "--markdown-note-mode",
        choices=["final", "final-lite", "final-explained"],
        default="final",
        help="Standalone Markdown note mode. Semantic modes also generate a compact semantic rebuild packet for agent rewriting.",
    )
    parser.add_argument(
        "--lightweight-teacher-review",
        action="store_true",
        help="Prepare lightweight teacher-stream review materials for risky course-transcript segments",
    )
    parser.add_argument(
        "--teacher-review-max-windows",
        type=int,
        default=4,
        help="Maximum number of teacher-review windows to prepare",
    )
    parser.add_argument("--download-video", action="store_true", help="Download the preferred replay video")
    parser.add_argument("--download-ppt-video", action="store_true", help="Download the PPT video when available")
    return parser.parse_args()


def parse_livingroom_url(url: str) -> dict[str, str]:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    result = {
        "course_id": query.get("course_id", [""])[0],
        "sub_id": query.get("sub_id", [""])[0],
        "tenant_code": query.get("tenant_code", [""])[0] or "21",
    }
    if not result["course_id"] or not result["sub_id"]:
        raise SystemExit("URL must include course_id and sub_id")
    return result


def browser_candidates(preferred: str) -> list[str]:
    if preferred == "auto":
        return ["msedge", "chrome"]
    return [preferred]


def resolve_browser_config(preferred: str, *, require_local_state: bool = False, require_cookie_db: bool = False) -> tuple[str, dict[str, Any]]:
    for name in browser_candidates(preferred):
        config = BROWSER_CONFIGS[name]
        if require_local_state and not Path(config["local_state"]).exists():
            continue
        if require_cookie_db and not Path(config["cookie_db"]).exists():
            continue
        return name, config
    if preferred == "auto":
        raise FileNotFoundError("No supported Chromium browser profile was found")
    raise FileNotFoundError(f"No supported profile data was found for browser channel '{preferred}'")


def get_master_key(local_state_path: Path) -> bytes:
    if current_platform() != "windows" or win32crypt is None:
        raise RuntimeError(
            "Direct browser cookie decryption is currently Windows-only; on macOS/Linux use --browser-runtime-auth instead"
        )
    local_state = json.loads(local_state_path.read_text(encoding="utf-8"))
    encrypted_key = base64.b64decode(local_state["os_crypt"]["encrypted_key"])[5:]
    return win32crypt.CryptUnprotectData(encrypted_key, None, None, None, 0)[1]


def decrypt_cookie_value(encrypted_value: bytes, master_key: bytes) -> str:
    if encrypted_value[:3] in (b"v10", b"v11"):
        nonce = encrypted_value[3:15]
        ciphertext = encrypted_value[15:-16]
        tag = encrypted_value[-16:]
        cipher = AES.new(master_key, AES.MODE_GCM, nonce=nonce)
        return cipher.decrypt_and_verify(ciphertext, tag).decode("utf-8", errors="ignore")
    if win32crypt is None:
        return ""
    try:
        return win32crypt.CryptUnprotectData(encrypted_value, None, None, None, 0)[1].decode(
            "utf-8", errors="ignore"
        )
    except Exception:
        return ""


def load_cookie_rows_from_db(cookie_db_path: Path, temp_copy_name: str) -> tuple[list[tuple[str, str, str, bytes, int]], str]:
    tmp_db = Path(tempfile.gettempdir()) / temp_copy_name
    conn: sqlite3.Connection | None = None
    source = "live_copy"
    try:
        shutil.copy2(cookie_db_path, tmp_db)
        conn = sqlite3.connect(str(tmp_db))
    except (PermissionError, OSError):
        if tmp_db.exists():
            conn = sqlite3.connect(str(tmp_db))
            source = "temp_copy"
        else:
            uri = cookie_db_path.resolve().as_uri()
            last_exc: sqlite3.Error | None = None
            for options in ("?mode=ro", "?mode=ro&nolock=1", "?mode=ro&immutable=1"):
                try:
                    conn = sqlite3.connect(uri + options, uri=True)
                    source = f"readonly:{options.lstrip('?')}"
                    break
                except sqlite3.Error as exc:
                    last_exc = exc
            if conn is None:
                raise last_exc or sqlite3.OperationalError("unable to open database file")
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT host_key, path, name, encrypted_value, is_secure "
            "FROM cookies WHERE host_key LIKE '%buaa.edu.cn%' OR host_key LIKE '%msa.buaa.edu.cn%'"
        )
        rows = cur.fetchall()
    finally:
        conn.close()
    return rows, source


def authorization_expiry(authorization: str) -> int | None:
    if not authorization.startswith("Bearer "):
        return None
    parts = authorization.split(" ", 1)[1].split(".")
    if len(parts) != 3:
        return None
    payload = parts[1]
    payload += "=" * (-len(payload) % 4)
    try:
        decoded = base64.urlsafe_b64decode(payload.encode("ascii"))
        data = json.loads(decoded.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return None
    exp = data.get("exp")
    return int(exp) if isinstance(exp, (int, float)) else None


def session_cache_payload(cookie_entries: list[dict[str, Any]], authorization: str) -> dict[str, Any]:
    return {
        "source": "edge_cookie_cache",
        "saved_at": int(time.time()),
        "cookie_count": len(cookie_entries),
        "authorization": authorization,
        "authorization_exp": authorization_expiry(authorization),
        "cookies": cookie_entries,
    }


def save_session_cache(cookie_entries: list[dict[str, Any]], authorization: str) -> None:
    SESSION_CACHE.parent.mkdir(parents=True, exist_ok=True)
    payload = session_cache_payload(cookie_entries, authorization)
    SESSION_CACHE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_session_cache() -> dict[str, Any]:
    if not SESSION_CACHE.exists():
        return {}
    try:
        payload = json.loads(SESSION_CACHE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, dict):
        return {}
    cookies = payload.get("cookies", [])
    if not isinstance(cookies, list) or not cookies:
        return {}
    authorization_exp = payload.get("authorization_exp")
    if isinstance(authorization_exp, (int, float)) and int(authorization_exp) <= int(time.time()):
        return {}
    return payload


def apply_cookie_entries(session: requests.Session, cookie_entries: list[dict[str, Any]]) -> None:
    for item in cookie_entries:
        session.cookies.set(
            str(item.get("name", "")),
            str(item.get("value", "")),
            domain=str(item.get("domain", "")),
            path=str(item.get("path", "/")),
            secure=bool(item.get("secure", False)),
        )


def runtime_cookie_entries(cookies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for item in cookies:
        domain = str(item.get("domain", ""))
        if "buaa.edu.cn" not in domain:
            continue
        entries.append(
            {
                "domain": domain,
                "path": str(item.get("path", "/")),
                "name": str(item.get("name", "")),
                "value": str(item.get("value", "")),
                "secure": bool(item.get("secure", False)),
            }
        )
    return entries


def runtime_session_is_authorized(session: requests.Session, referer: str) -> bool:
    try:
        params = parse_livingroom_url(referer)
    except SystemExit:
        return bool(session.headers.get("Authorization"))
    try:
        resp = session.get(
            f"https://classroom.msa.buaa.edu.cn/coursesourceapi/course/study-auth/{params['course_id']}/{params['sub_id']}",
            timeout=20,
        )
    except requests.RequestException:
        return False
    if resp.status_code != 200:
        return False
    try:
        payload = resp.json()
    except json.JSONDecodeError:
        return False
    return bool(payload.get("data", {}).get("hasPermission"))


def build_runtime_session(referer: str, profile_dir: Path, login_timeout: int, browser_channel: str) -> requests.Session:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise SystemExit("Playwright is not available, so dedicated browser runtime auth cannot be used") from exc

    profile_dir.mkdir(parents=True, exist_ok=True)
    last_error = ""

    with sync_playwright() as playwright:
        for browser_name in browser_candidates(browser_channel):
            config = BROWSER_CONFIGS[browser_name]
            try:
                print(
                    f"Launching dedicated {config['display_name']} runtime-auth window. "
                    "If BUAA is not logged in there, complete login in that window and keep it open until extraction resumes."
                )
                context = playwright.chromium.launch_persistent_context(
                    user_data_dir=str(profile_dir),
                    channel=str(config["playwright_channel"]),
                    headless=False,
                )
            except Exception as exc:
                last_error = str(exc)
                continue

            try:
                page = context.pages[0] if context.pages else context.new_page()
                page.goto(referer, wait_until="domcontentloaded", timeout=30000)
                deadline = time.time() + max(10, login_timeout)
                while time.time() < deadline:
                    cookie_entries = runtime_cookie_entries(context.cookies())
                    session = requests.Session()
                    session.headers.update(
                        {
                            "User-Agent": (
                                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                "AppleWebKit/537.36 (KHTML, like Gecko) "
                                "Chrome/145.0.0.0 Safari/537.36"
                            ),
                            "Referer": referer,
                        }
                    )
                    apply_cookie_entries(session, cookie_entries)
                    authorization = populate_auth_headers(session)
                    authorization_exp = authorization_expiry(authorization)
                    if (
                        cookie_entries
                        and authorization
                        and authorization_exp
                        and authorization_exp > int(time.time())
                        and runtime_session_is_authorized(session, referer)
                    ):
                        save_session_cache(cookie_entries, authorization)
                        setattr(session, "_codex_auth_source", f"{browser_name}:runtime")
                        setattr(session, "_codex_runtime_auth_enabled", False)
                        return session
                    time.sleep(2)
            finally:
                context.close()

    if last_error:
        raise SystemExit(
            f"Could not launch a supported Chromium browser for runtime auth: {last_error}"
        )
    raise SystemExit(
        "Timed out waiting for BUAA login in the dedicated browser runtime-auth window. "
        "Log in there and rerun the command."
    )


def populate_auth_headers(session: requests.Session, fallback_authorization: str = "") -> str:
    token_cookie = session.cookies.get("_token")
    if token_cookie:
        decoded = unquote(token_cookie)
        match = re.search(r'{i:\d+;s:\d+:"_token";i:\d+;s:\d+:"(.+?)";}', decoded)
        if match:
            authorization = f"Bearer {match.group(1)}"
            session.headers["Authorization"] = authorization
            return authorization
    if fallback_authorization:
        session.headers["Authorization"] = fallback_authorization
        return fallback_authorization
    return ""


def build_session(
    referer: str,
    *,
    allow_runtime_auth: bool = False,
    runtime_profile_dir: Path | None = None,
    runtime_login_timeout: int = 180,
    browser_channel: str = "auto",
) -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/145.0.0.0 Safari/537.36"
            ),
            "Referer": referer,
        }
    )
    setattr(session, "_codex_runtime_auth_enabled", allow_runtime_auth)
    setattr(session, "_codex_runtime_profile_dir", str(runtime_profile_dir or RUNTIME_BROWSER_PROFILE))
    setattr(session, "_codex_runtime_login_timeout", int(runtime_login_timeout))
    setattr(session, "_codex_runtime_referer", referer)
    setattr(session, "_codex_browser_channel", browser_channel)
    live_error = ""
    try:
        browser_name, browser_config = resolve_browser_config(
            browser_channel, require_local_state=True, require_cookie_db=True
        )
        master_key = get_master_key(Path(browser_config["local_state"]))
        rows, cookie_source = load_cookie_rows_from_db(
            Path(browser_config["cookie_db"]),
            str(browser_config["temp_cookie_copy"]),
        )
        cookie_entries: list[dict[str, Any]] = []
        for host, path, name, encrypted, secure in rows:
            value = decrypt_cookie_value(encrypted, master_key)
            if not value:
                continue
            session.cookies.set(name, value, domain=host, path=path or "/", secure=bool(secure))
            cookie_entries.append(
                {
                    "domain": host,
                    "path": path or "/",
                    "name": name,
                    "value": value,
                    "secure": bool(secure),
                }
            )
        authorization = populate_auth_headers(session)
        authorization_exp = authorization_expiry(authorization)
        if authorization_exp is not None and authorization_exp <= int(time.time()):
            raise RuntimeError(
                "The available BUAA login session has already expired. Refresh the login session once "
                "(for example by rerunning with browser runtime auth), then later runs can reuse the cache while the browser stays open."
            )
        if cookie_entries:
            save_session_cache(cookie_entries, authorization)
        setattr(session, "_codex_auth_source", f"{browser_name}:{cookie_source}")
        return session
    except (PermissionError, OSError, sqlite3.Error, RuntimeError, FileNotFoundError) as exc:
        live_error = str(exc)
        cached = load_session_cache()
        cookies = cached.get("cookies", [])
        if cookies:
            apply_cookie_entries(session, cookies)
            populate_auth_headers(session, str(cached.get("authorization", "") or ""))
            setattr(session, "_codex_auth_source", "cached")
            return session
        if allow_runtime_auth:
            return build_runtime_session(
                referer,
                runtime_profile_dir or RUNTIME_BROWSER_PROFILE,
                runtime_login_timeout,
                browser_channel,
            )
        raise SystemExit(
            live_error
            or "The browser cookie database is unavailable and no valid cached BUAA session exists yet; "
            "rerun with browser runtime auth to refresh the cache"
        )


def fetch_json(session: requests.Session, url: str, **kwargs: Any) -> Any:
    resp = session.get(url, timeout=30, **kwargs)
    if resp.status_code in {401, 403}:
        auth_source = str(getattr(session, "_codex_auth_source", ""))
        if bool(getattr(session, "_codex_runtime_auth_enabled", False)) and not auth_source.endswith(":runtime"):
            resp.close()
            refreshed = build_runtime_session(
                str(getattr(session, "_codex_runtime_referer", "")),
                Path(str(getattr(session, "_codex_runtime_profile_dir", RUNTIME_BROWSER_PROFILE))),
                int(getattr(session, "_codex_runtime_login_timeout", 180)),
                str(getattr(session, "_codex_browser_channel", "auto")),
            )
            return fetch_json(refreshed, url, **kwargs)
        if auth_source == "cached" or auth_source.endswith(":temp_copy"):
            raise SystemExit(
                "The cached BUAA session is no longer valid. Refresh the login session once "
                "(for example by rerunning with browser runtime auth), then later runs can reuse the cache while the browser stays open."
            )
    resp.raise_for_status()
    return resp.json()


def sanitize_filename(name: str) -> str:
    name = re.sub(r'[<>:"/\\\\|?*]+', "_", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name[:120] or "buaa_video"


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.rstrip() + "\n", encoding="utf-8")


def download_file(session: requests.Session, url: str, target: Path) -> None:
    with session.get(url, stream=True, timeout=60) as resp:
        resp.raise_for_status()
        with target.open("wb") as f:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)


def flatten_transcript(transcript_payload: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    merged: list[dict[str, Any]] = []
    seen: set[tuple[Any, Any, Any]] = set()
    for entry in transcript_payload.get("list", []):
        for item in entry.get("all_content", []):
            key = (item.get("BeginSec"), item.get("EndSec"), item.get("Text"))
            if key in seen:
                continue
            seen.add(key)
            merged.append(
                {
                    "begin_sec": item.get("BeginSec"),
                    "end_sec": item.get("EndSec"),
                    "text": item.get("Text", ""),
                    "trans_text": item.get("TransText", ""),
                }
            )
    merged.sort(key=lambda x: (x["begin_sec"] if x["begin_sec"] is not None else 10**12))
    text = "\n".join(item["text"] for item in merged if item["text"])
    return merged, text


def unique_keep_order(items: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def format_seconds(total_seconds: float | int | str) -> str:
    try:
        value = max(0, int(float(total_seconds)))
    except (TypeError, ValueError):
        return ""
    hours, remainder = divmod(value, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def lesson_date_from_timestamp(raw: Any) -> str:
    try:
        timestamp = int(str(raw or "0"))
    except ValueError:
        return ""
    if timestamp <= 0:
        return ""
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d")


def slugify_outline_line(text: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", text or "", flags=re.UNICODE).lower()


def clean_outline_line(text: str) -> str:
    line = re.sub(r"\s+", " ", str(text or "")).strip(" -:：;；,.，。")
    if not line:
        return ""
    lower = line.lower()
    banned_substrings = [
        "buaa",
        "school of",
        "mathematical sciences",
        "zhangsirong",
        "@",
        "march ",
        "cdu.cn",
    ]
    if any(part in lower for part in banned_substrings):
        return ""
    if re.fullmatch(r"[0-9:./, -]{2,}", line):
        return ""
    if len(line) <= 1:
        return ""
    if not re.search(r"[\u4e00-\u9fff]", line):
        return ""
    if len(line) <= 2 and not re.search(r"[\u4e00-\u9fffA-Za-z]{2,}", line):
        return ""
    return line


def load_outline_slides(outline_dir: Path) -> list[dict[str, Any]]:
    outline_json = outline_dir / "ppt_outline.json"
    if not outline_json.exists():
        return []
    try:
        data = json.loads(outline_json.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def build_outline_groups(slides: list[dict[str, Any]]) -> list[dict[str, Any]]:
    line_counts: dict[str, int] = {}
    prepared: list[tuple[dict[str, Any], list[str]]] = []
    for slide in slides:
        raw_lines = slide.get("ocr_lines", []) or []
        cleaned_lines = unique_keep_order([clean_outline_line(line) for line in raw_lines if clean_outline_line(line)])
        if not cleaned_lines:
            preview = clean_outline_line(slide.get("ocr_preview", ""))
            if preview:
                cleaned_lines = [preview]
        prepared.append((slide, cleaned_lines))
        for line in cleaned_lines:
            key = slugify_outline_line(line)
            if key:
                line_counts[key] = line_counts.get(key, 0) + 1

    groups: list[dict[str, Any]] = []
    previous_key = ""
    for slide, cleaned_lines in prepared:
        cleaned_lines = [
            line
            for line in cleaned_lines
            if line_counts.get(slugify_outline_line(line), 0) <= 3 or len(line) <= 12
        ]
        if not cleaned_lines:
            continue
        heading = cleaned_lines[0]
        key = slugify_outline_line(heading)
        if key and key == previous_key and groups:
            group = groups[-1]
            group["end_sec"] = slide.get("timestamp_sec", group["end_sec"])
            group["slides"].append(slide.get("file_name", ""))
            group["points"] = unique_keep_order(group["points"] + cleaned_lines[1:4])
            continue
        groups.append(
            {
                "heading": heading,
                "start_sec": slide.get("timestamp_sec", 0),
                "end_sec": slide.get("timestamp_sec", 0),
                "points": cleaned_lines[1:4],
                "slides": [slide.get("file_name", "")],
            }
        )
        previous_key = key
    for idx, group in enumerate(groups[:-1]):
        next_start = groups[idx + 1]["start_sec"]
        if next_start and next_start > group["start_sec"]:
            group["end_sec"] = next_start
    return groups


def clean_transcript_line(text: str) -> str:
    line = re.sub(r"\s+", " ", str(text or "")).strip(" -:：;；,.，。")
    if not line or len(line) <= 1:
        return ""
    if line in {"谢谢", "谢谢大家", "对吧", "嗯", "啊", "好"}:
        return ""
    return line


def transcript_lines_in_range(
    transcript_segments: list[dict[str, Any]], start_sec: float, end_sec: float, limit: int = 18
) -> list[str]:
    selected: list[str] = []
    if end_sec <= start_sec:
        end_sec = start_sec + 600
    for item in transcript_segments:
        try:
            begin = float(item.get("begin_sec") or 0)
        except (TypeError, ValueError):
            begin = 0
        if begin < max(0.0, start_sec - 12) or begin > end_sec + 12:
            continue
        text = clean_transcript_line(item.get("text", ""))
        if not text:
            continue
        selected.append(text)
        if len(selected) >= limit:
            break
    return unique_keep_order(selected)


def transcript_overview_payload(transcript_segments: list[dict[str, Any]]) -> dict[str, Any]:
    lines = [clean_transcript_line(item.get("text", "")) for item in transcript_segments]
    lines = [line for line in lines if line]
    return {
        "segment_count": len(transcript_segments),
        "sample_lines": unique_keep_order(lines)[:8],
    }


def lesson_duration_seconds(metadata: dict[str, Any]) -> float:
    raw_duration = metadata.get("duration")
    try:
        duration = float(raw_duration)
        if duration > 0:
            return duration
    except (TypeError, ValueError):
        pass
    try:
        start_at = float(metadata.get("start_at") or 0)
        end_at = float(metadata.get("end_at") or 0)
        if end_at > start_at > 0:
            return end_at - start_at
    except (TypeError, ValueError):
        pass
    return 0.0


def transcript_coverage_info(metadata: dict[str, Any], transcript_segments: list[dict[str, Any]]) -> dict[str, Any]:
    duration_sec = lesson_duration_seconds(metadata)
    last_sec = 0.0
    for item in transcript_segments:
        try:
            end_sec = float(item.get("end_sec") or item.get("begin_sec") or 0)
        except (TypeError, ValueError):
            end_sec = 0.0
        last_sec = max(last_sec, end_sec)
    ratio = (last_sec / duration_sec) if duration_sec > 0 else 0.0
    missing_sec = max(0.0, duration_sec - last_sec)
    insufficient = bool(
        transcript_segments
        and duration_sec >= 1800
        and ratio < 0.35
        and missing_sec >= 1800
    )
    return {
        "duration_sec": round(duration_sec, 2),
        "last_transcript_sec": round(last_sec, 2),
        "coverage_ratio": round(ratio, 4),
        "missing_tail_sec": round(missing_sec, 2),
        "insufficient": insufficient,
    }


def summary_coverage_info(
    transcript_segments: list[dict[str, Any]],
    sections: list[dict[str, Any]],
) -> dict[str, Any]:
    last_transcript_sec = 0.0
    for item in transcript_segments:
        try:
            end_sec = float(item.get("end_sec") or item.get("begin_sec") or 0)
        except (TypeError, ValueError):
            end_sec = 0.0
        last_transcript_sec = max(last_transcript_sec, end_sec)
    if not transcript_segments or not sections or last_transcript_sec <= 0:
        return {
            "covered_until_sec": 0.0,
            "coverage_ratio": 0.0,
            "max_internal_gap_sec": 0.0,
            "missing_tail_sec": round(last_transcript_sec, 2),
            "insufficient": True,
        }
    ordered = sorted(
        (
            {
                "start_sec": float(section.get("start_sec", 0) or 0),
                "end_sec": float(section.get("end_sec", 0) or 0),
            }
            for section in sections
        ),
        key=lambda item: item["start_sec"],
    )
    covered_until = max(item["end_sec"] for item in ordered)
    max_gap_sec = 0.0
    previous_end = 0.0
    for item in ordered:
        max_gap_sec = max(max_gap_sec, max(0.0, item["start_sec"] - previous_end))
        previous_end = max(previous_end, item["end_sec"])
    missing_tail_sec = max(0.0, last_transcript_sec - covered_until)
    coverage_ratio = covered_until / last_transcript_sec if last_transcript_sec > 0 else 0.0
    insufficient = bool(
        coverage_ratio < 0.85
        or missing_tail_sec >= 900
        or max_gap_sec >= 900
    )
    return {
        "covered_until_sec": round(covered_until, 2),
        "coverage_ratio": round(coverage_ratio, 4),
        "max_internal_gap_sec": round(max_gap_sec, 2),
        "missing_tail_sec": round(missing_tail_sec, 2),
        "insufficient": insufficient,
    }


def build_replay_diagnosis(
    metadata: dict[str, Any],
    transcript_segments: list[dict[str, Any]],
    outline_slides: list[dict[str, Any]],
) -> dict[str, Any]:
    coverage = transcript_coverage_info(metadata, transcript_segments)
    has_transcript = bool(transcript_segments)
    has_ppt_artifact = bool(outline_slides)
    if not has_transcript:
        status = "waiting_transcript"
        section_strategy = "waiting_only"
        draft_basis = "waiting_transcript"
    elif coverage["insufficient"]:
        status = "partial_transcript"
        section_strategy = "partial_only"
        draft_basis = "partial_transcript_only"
    else:
        status = "transcript_only"
        section_strategy = "transcript_topic"
        draft_basis = "transcript_primary"
    return {
        "status": status,
        "source_profile": status,
        "draft_basis": draft_basis,
        "section_strategy": section_strategy,
        "has_transcript": has_transcript,
        "has_ppt_artifact": has_ppt_artifact,
        "has_ppt_outline": has_ppt_artifact,
        "coverage": coverage,
    }


def transcript_mentions(lines: list[str], keywords: list[str]) -> bool:
    joined = " ".join(lines)
    return any(keyword in joined for keyword in keywords)


PRESENTATION_KEYWORDS = [
    "汇报",
    "汇报人",
    "展示",
    "我们组",
    "同学",
    "点评",
    "小组",
]
UI_NOISE_TOKENS = [
    "新建",
    "模板",
    "单页",
    "字体",
    "形状",
    "排列",
    "保存到",
    "粘贴",
    "加载项",
    "剪贴板",
    "pdf转换",
    "powerpoint",
    "officeplus",
    "chatgpt",
    "百度网盘",
]

def infer_section_role(section: dict[str, Any], transcript_lines: list[str]) -> str:
    text_parts = [str(section.get("title") or "")]
    text_parts.extend(str(item) for item in section.get("headings", []))
    text_parts.extend(str(item) for item in section.get("points", []))
    text_parts.extend(transcript_lines)
    text = " ".join(text_parts)
    presentation_hits = sum(text.count(keyword) for keyword in PRESENTATION_KEYWORDS)
    ui_noise_hits = sum(text.lower().count(token.lower()) for token in UI_NOISE_TOKENS)
    if "汇报人" in text or presentation_hits >= 3:
        return "presentation"
    if ui_noise_hits >= 4:
        return "presentation"
    if any(keyword in text for keyword in ["作业", "考试", "考核", "通知", "截止"]):
        return "logistics"
    return "lecture"


def display_section_title(section: dict[str, Any]) -> str:
    if section.get("role") == "presentation":
        return "课堂展示与教师点评"
    if section.get("kind") == "transcript_topic":
        index = section.get("display_index")
        if index:
            return f"课堂讲解与主题推进 {index}"
        return "课堂讲解与主题推进"
    return str(section.get("title") or "")


def section_kind_from_heading(heading: str) -> str:
    if any(token in heading for token in ["课程简介", "课程定位", "学习方式", "课程安排", "为什么要学"]):
        return "course_design"
    if any(token in heading for token in ["简介", "导论", "是什么"]):
        return "intro"
    if any(token in heading for token in ["基础", "背景", "回顾", "预备知识", "定义", "概念", "原理"]):
        return "foundations"
    if any(token in heading for token in ["方法", "推导", "证明", "构造", "性质", "分析", "实现", "设计"]):
        return "inference"
    if any(token in heading.lower() for token in ["example"]) or any(token in heading for token in ["实例", "案例"]):
        return "example"
    if any(token in heading for token in ["一般过程", "流程", "步骤", "框架", "思路"]):
        return "workflow"
    return "generic"


def final_section_title(kind: str, heading: str) -> str:
    mapping = {
        "intro": "本节主题与问题背景",
        "course_design": "课程定位与学习安排",
        "foundations": "本节涉及的基础概念与背景",
        "inference": "主要方法与推导思路",
        "example": "例子与应用场景",
        "workflow": "方法流程与整体框架",
    }
    return mapping.get(kind, heading)


def build_final_sections(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    for group in groups:
        kind = section_kind_from_heading(group["heading"])
        title = final_section_title(kind, group["heading"])
        if sections and sections[-1]["title"] == title:
            section = sections[-1]
            section["end_sec"] = group.get("end_sec", section["end_sec"])
            section["headings"] = unique_keep_order(section["headings"] + [group["heading"]])
            section["points"] = unique_keep_order(section["points"] + group.get("points", []))
            continue
        sections.append(
            {
                "kind": kind,
                "title": title,
                "start_sec": group.get("start_sec", 0),
                "end_sec": group.get("end_sec", 0),
                "headings": [group["heading"]],
                "points": list(group.get("points", [])),
            }
        )
    return sections


def build_transcript_fallback_sections(transcript_segments: list[dict[str, Any]], transcript_text: str) -> list[dict[str, Any]]:
    text = transcript_text or "\n".join(str(item.get("text", "")) for item in transcript_segments)
    if not text.strip():
        return []
    cleaned_segments = []
    for item in transcript_segments:
        line = clean_transcript_line(item.get("text", ""))
        if not line:
            continue
        cleaned_segments.append({**item, "clean_text": line})
    if not cleaned_segments:
        return []
    chunk_count = min(6, max(2, (len(cleaned_segments) + 9) // 10))
    chunk_size = max(1, (len(cleaned_segments) + chunk_count - 1) // chunk_count)
    sections: list[dict[str, Any]] = []
    for index, start in enumerate(range(0, len(cleaned_segments), chunk_size), start=1):
        chunk = cleaned_segments[start : start + chunk_size]
        if not chunk:
            continue
        lines = [str(item.get("clean_text") or "") for item in chunk if str(item.get("clean_text") or "")]
        title = f"转写分段{index}"
        start_sec = float(chunk[0].get("begin_sec") or 0)
        end_sec = float(chunk[-1].get("end_sec") or chunk[-1].get("begin_sec") or start_sec)
        sections.append(
            {
                "kind": "transcript_topic",
                "title": title,
                "source_title": title,
                "display_index": index,
                "start_sec": start_sec,
                "end_sec": end_sec,
                "headings": [title],
                "points": [],
                "sample_lines": unique_keep_order(lines)[:3],
            }
        )
    return sections


def build_final_mainline(sections: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for section in sections[:6]:
        role = section.get("role", "lecture")
        if role == "presentation":
            lines.append("这一段以课堂展示和教师点评为主，整理时应区分展示材料、老师评价和课程正式结论。")
            continue
        if role == "logistics":
            lines.append("这一段夹带了课程事务或组织安排，知识内容和事务信息应分开记录。")
            continue
        kind = section["kind"]
        if kind == "intro":
            lines.append("先交代本节要解决的核心问题，以及这部分内容在整门课中的位置。")
        elif kind == "course_design":
            lines.append("说明课程安排、学习方式或这节课的组织方式，帮助读者把后续内容放回教学上下文。")
        elif kind == "foundations":
            lines.append("回顾理解后续内容所需的基础概念、定义和背景。")
        elif kind == "inference":
            lines.append("整理老师在这一段真正推进的方法、推导思路或关键结论。")
        elif kind == "example":
            lines.append("用例子或应用场景解释抽象概念为什么重要。")
        elif kind == "workflow":
            lines.append("把零散内容收束成更完整的流程、框架或方法链。")
        elif kind == "transcript_topic":
            lines.append("这一段以老师连续讲解为主，正式主线应结合整节课程转写和课程上下文来重建。")
        else:
            lines.append(f"围绕“{section['title']}”整理本节对应的教学段落。")
    return unique_keep_order(lines)


def render_final_section_bullets(section: dict[str, Any], transcript_lines: list[str]) -> list[str]:
    role = section.get("role", "lecture")
    if role == "presentation":
        return [
            "这一段以学生展示和老师即时点评为主，不宜把展示材料里的标题、软件界面或个别案例细节直接当成课程概念。",
            "整理时应优先提炼老师借展示反复强调的方法判断、比较标准或限制条件，而不是逐页复述展示内容。",
        ]
    if role == "logistics":
        return [
            "这一段主要涉及课程事务或组织安排，应与正式知识主线分开记录。",
        ]
    kind = section["kind"]
    bullets: list[str] = []
    if kind == "intro":
        bullets.append("老师先交代这一部分内容为什么重要，以及它在整门课中的作用。")
        if transcript_mentions(transcript_lines, ["背景", "动机", "问题"]):
            bullets.append("从转写看，这一段更偏背景说明和问题引入，而不是直接进入细节证明。")
        return bullets
    if kind == "course_design":
        bullets.append("这一段主要在说明课程安排、学习方式或本节课的组织方式。")
        if transcript_mentions(transcript_lines, ["案例", "讨论", "交流"]):
            bullets.append("老师提到这部分内容会结合案例、讨论或交流，不会只停留在板书或定义层面。")
        if transcript_mentions(transcript_lines, ["作业", "考核", "考试"]):
            bullets.append("这一段还夹带了一些课程事务信息，后续整理时应和知识内容分开记录。")
        return bullets
    if kind == "foundations":
        bullets.append("这一段主要在回顾后续内容所需的基本概念、定义或背景知识。")
        bullets.append("它的作用更像搭建统一语言，为后面的正式方法或结论做准备。")
        return bullets
    if kind == "inference":
        bullets.append("这一段开始进入方法本体，重点是弄清老师到底在构造什么对象、推进什么论证。")
        bullets.append("整理时应优先保留方法主线和关键结论，而不是机械抄录零散术语。")
        if transcript_mentions(transcript_lines, ["证明", "推导", "构造", "性质"]):
            bullets.append("从转写看，这里带有明显的推导或性质分析成分，后续重写时要把逻辑链说明白。")
        return bullets
    if kind == "example":
        bullets.append("老师用具体例子或应用场景帮助学生理解抽象概念如何落地。")
        bullets.append("这段更适合提炼“例子说明了什么”，而不是逐句复述情境细节。")
        return bullets
    if kind == "workflow":
        bullets.append("这一段把前面的零散内容收束成更清晰的步骤、流程或总体框架。")
        bullets.append("整理时应突出“先做什么、再做什么、为什么这样连接”，而不是简单罗列标题。")
        return bullets
    if kind == "transcript_topic":
        bullets.append("这一段以老师连续讲解为主，整理时应结合前后时段重建真正推进的问题、方法或结论。")
        bullets.append("这里保留时间范围，便于后续语义重建、回听和与课程上下文对齐。")
        return bullets
    bullets.append(f"这一段主要围绕“{section['title']}”展开。")
    if section.get("points"):
        bullets.append(f"从 PPT 看，核心点包括：{'、'.join(section['points'][:3])}。")
    if transcript_lines:
        bullets.append(f"转写显示，这一段更多是在口头解释“{section['title']}”为什么重要，而不只是罗列结论。")
    return bullets


def build_replay_affairs_summary(transcript_text: str) -> dict[str, list[str]]:
    text = transcript_text or ""
    assignment: list[str] = []
    exam: list[str] = []
    arrangement: list[str] = []
    notice: list[str] = []

    if "大作业" in text:
        assignment.append("转写里可以较稳定地确认：课程会有一项大作业。")
        weight_match = re.search(r"(\d{1,2})\s*%\s*(\d{1,2})\s*%", text)
        if weight_match:
            assignment.append(
                f"目前较可信的说法是大作业权重大约在 `{weight_match.group(1)}%-{weight_match.group(2)}%`，但具体占比和提交方式需要后续再核对。"
            )
    if not assignment:
        assignment.append("当前未从转写中识别出稳定的作业信息。")

    if any(keyword in text for keyword in ["课堂考试", "考试内容", "考核"]):
        exam.append("这一段转写噪声较大，暂时不把具体考试形式写死。")
        exam.append("目前只能保守记为：课程考核不止一种形式，后面还会进一步明确课堂考核或阶段性考核安排。")
    if not exam:
        exam.append("当前未从转写中识别出稳定的考试安排。")

    if any(keyword in text for keyword in ["案例分析", "案例", "讨论", "交流"]):
        arrangement.append("老师提到课程中会穿插案例分析、讨论或交流环节。")
    if any(keyword in text for keyword in ["实验", "展示", "汇报", "延伸"]):
        arrangement.append("转写显示课程可能还包含展示、实验或延伸讨论等安排，具体以后续课堂说明为准。")
    if not arrangement:
        arrangement.append("当前未从转写中识别出稳定的课程安排信息。")

    if "下周" in text and "小教室" in text:
        notice.append("转写里提到下周换到更适合交流的小教室上课，但这类行政安排最好以后续课程页面或教师口头说明再确认一次。")
    if not notice:
        notice.append("当前未从转写中识别出稳定的课程通知。")

    return {
        "assignment": assignment,
        "exam": exam,
        "arrangement": arrangement,
        "notice": notice,
    }


def append_transcript_tail_section_if_needed(
    sections: list[dict[str, Any]],
    transcript_segments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not sections or not transcript_segments:
        return sections
    non_presentation = [section for section in sections if section.get("role") != "presentation"]
    if non_presentation:
        return sections
    last_section_end = max(float(section.get("end_sec", 0)) for section in sections)
    transcript_end = max(float(item.get("end_sec") or item.get("begin_sec") or 0) for item in transcript_segments)
    if transcript_end - last_section_end < 1800:
        return sections
    tail_start = last_section_end + 30
    sections.append(
        {
            "kind": "inference",
            "title": "老师后续讲解与方法推进",
            "display_title": "老师后续讲解与方法推进",
            "role": "lecture",
            "start_sec": tail_start,
            "end_sec": transcript_end,
            "headings": ["老师后续讲解与方法推进"],
            "points": [],
        }
    )
    return sections


def build_final_review_items(transcript_text: str, sections: list[dict[str, Any]], has_ppt_outline: bool) -> list[str]:
    text = transcript_text or ""
    review_items: list[str] = []
    if "大作业" in text or any(keyword in text for keyword in ["考试", "考核"]):
        review_items.append("课程考核细节的转写噪声较大，目前只能较确定地看出“有一项大作业”，其余比例和形式需回看确认。")
    review_items.append("这份纪要的章节边界和主线判断应始终以课程转写为准；如果有 PPT，也只能作为术语、标题、公式符号和事务截图的辅助参考。")
    if any(keyword in text for keyword in ["讨论", "案例", "交流"]):
        review_items.append("课堂上提到会安排讨论与案例分析，但这类课程安排通常比知识细节更值得后续继续跟踪。")
    if not review_items:
        review_items.append("当前未发现必须立刻复核的事务信息。")
    return review_items


def build_semantic_rebuild_prompt(mode: str) -> str:
    concept_rule = (
        "- 只对经你确认的核心概念补 1 句面向学生的语境化解释，解释它在本节里起什么作用，不要写成教材式长定义。"
        if mode == "final-explained"
        else "- 只保留经你确认的核心概念，不额外扩写长解释。"
    )
    return "\n".join(
        [
            f"# Semantic Rebuild Prompt ({mode})",
            "",
            "请基于 `semantic_rebuild_input.json` 重写独立 Markdown 课次纪要，遵守以下约束：",
            "",
            "- 课程转写永远是唯一主来源；PPT 只作辅助校正。",
            "- PPT 只允许补术语拼写、书名或页面标题、公式符号、课程事务类截图信息。",
            "- 不要让 PPT 决定 section 边界、主线、概念提取或课次完成状态。",
            "- 不要把 OCR 碎句或 ASR 噪声原样抄进正文。",
            "- 主线和内容纪要要像人类学生整理后的课程纪要，而不是关键词拼接。",
            "- 如果 `has_ppt_outline=false`，不要套用通用课程模板标题；应根据 `sections`、`transcript_overview`、各段课程转写片段和课程上下文自行归纳 3 到 6 个真实主题。",
            "- `内容纪要` 的每个分段都必须保留时间轴。优先沿用 packet 里已有的 `time_range`；如果时间只适合写成粗粒度区间，也要保留“时间参考：约 `MM:SS-MM:SS`”或同等清晰的时间标记。",
            "- 不要预设这门课属于统计、数学、工科或文科中的任何一类，先判断课程域，再写正文。",
            concept_rule,
            "- 事务信息只写高置信度结论；不确定项放进 `待核对`。",
            "- 不要补出课程转写 / PPT / 教师流片段都没有支持的新结论。",
        ]
    )


def write_semantic_rebuild_artifacts(output_dir: Path, packet: dict[str, Any], mode: str) -> dict[str, str]:
    semantic_dir = output_dir / "semantic_rebuild"
    semantic_dir.mkdir(parents=True, exist_ok=True)
    packet_path = semantic_dir / "semantic_rebuild_input.json"
    prompt_path = semantic_dir / "semantic_rebuild_prompt.md"
    write_text(packet_path, json.dumps(packet, ensure_ascii=False, indent=2))
    write_text(prompt_path, build_semantic_rebuild_prompt(mode))
    return {
        "dir": str(semantic_dir),
        "input_path": str(packet_path),
        "prompt_path": str(prompt_path),
    }


def compile_keyword_pattern(keywords: list[str]) -> re.Pattern[str]:
    return re.compile("|".join(re.escape(keyword) for keyword in keywords))


def review_keyword_groups() -> dict[str, dict[str, Any]]:
    return {
        "affairs_heavy": {
            "label": "事务密集",
            "keywords": ["作业", "大作业", "考试", "考核", "提交", "截止", "下周", "通知", "安排", "分组", "占比", "%"],
            "window_padding_sec": 30,
            "max_duration_sec": 120,
        },
    }


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def merge_review_hits(
    hits: list[dict[str, Any]],
    *,
    padding_sec: int,
    max_duration_sec: int,
    max_windows: int,
) -> list[dict[str, Any]]:
    if not hits:
        return []
    windows: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for hit in hits:
        begin = max(0.0, safe_float(hit.get("begin_sec")) - padding_sec)
        end = safe_float(hit.get("end_sec")) + padding_sec
        excerpt = str(hit.get("text", "")).strip()
        if current is None:
            current = {
                "start_sec": begin,
                "end_sec": end,
                "excerpts": [excerpt] if excerpt else [],
                "hit_count": 1,
            }
            continue
        if begin <= current["end_sec"] + 20:
            current["end_sec"] = max(current["end_sec"], end)
            if excerpt:
                current["excerpts"].append(excerpt)
            current["hit_count"] += 1
            continue
        windows.append(current)
        current = {
            "start_sec": begin,
            "end_sec": end,
            "excerpts": [excerpt] if excerpt else [],
            "hit_count": 1,
        }
    if current is not None:
        windows.append(current)

    normalized: list[dict[str, Any]] = []
    for window in windows:
        start_sec = float(window["start_sec"])
        end_sec = float(window["end_sec"])
        if end_sec - start_sec > max_duration_sec:
            end_sec = start_sec + max_duration_sec
        normalized.append(
            {
                "start_sec": round(start_sec, 2),
                "end_sec": round(end_sec, 2),
                "hit_count": int(window["hit_count"]),
                "excerpts": unique_keep_order([item for item in window["excerpts"] if item])[:3],
            }
        )
    normalized.sort(key=lambda item: (-int(item["hit_count"]), float(item["start_sec"])))
    return normalized[:max_windows]


def detect_teacher_review_windows(
    transcript_segments: list[dict[str, Any]],
    max_windows: int,
) -> dict[str, Any]:
    groups = review_keyword_groups()
    windows: list[dict[str, Any]] = []
    flags: list[str] = []
    per_flag_limit = 1
    for flag, spec in groups.items():
        pattern = compile_keyword_pattern(list(spec["keywords"]))
        hits = []
        for item in transcript_segments:
            text = clean_transcript_line(item.get("text", ""))
            if not text or not pattern.search(text):
                continue
            hits.append(
                {
                    "begin_sec": safe_float(item.get("begin_sec")),
                    "end_sec": safe_float(item.get("end_sec"), safe_float(item.get("begin_sec"))),
                    "text": text,
                }
            )
        merged = merge_review_hits(
            hits,
            padding_sec=int(spec["window_padding_sec"]),
            max_duration_sec=int(spec["max_duration_sec"]),
            max_windows=per_flag_limit,
        )
        if merged:
            flags.append(flag)
            for idx, window in enumerate(merged, start=1):
                windows.append(
                    {
                        "flag": flag,
                        "label": str(spec["label"]),
                        "rank": idx,
                        **window,
                    }
                )
    windows.sort(key=lambda item: (float(item["start_sec"]), item["flag"]))
    return {
        "flags": unique_keep_order(flags),
        "windows": windows[: max(1, max_windows)],
    }


def teacher_review_clip_name(flag: str, rank: int) -> str:
    return f"{flag}-{rank:02d}.mp4"


def build_teacher_review_questions(windows: list[dict[str, Any]]) -> list[str]:
    questions: list[str] = []
    for window in windows:
        joined = " ".join(str(item) for item in window.get("excerpts", []))
        if "大作业" in joined or "作业" in joined:
            questions.append("这节课是否明确说明了大作业的权重、提交方式或截止时间？")
        if "考试" in joined or "考核" in joined:
            questions.append("这节课是否明确说明了课堂考试或课程考核的次数、形式或占比？")
        if "下周" in joined or "通知" in joined:
            questions.append("这节课是否给出了下周安排、教室变更或其他行政通知的明确结论？")
    if not questions:
        questions.append("这些事务片段里是否有可以直接写进笔记的明确信息？")
    return unique_keep_order(questions)


def load_existing_teacher_review(review_dir: Path) -> dict[str, Any]:
    review_path = review_dir / "teacher_review.json"
    if not review_path.exists():
        return {}
    try:
        data = json.loads(review_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def extract_teacher_review_clip(video_url: str, target: Path, start_sec: float, end_sec: float) -> None:
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    target.parent.mkdir(parents=True, exist_ok=True)
    duration = max(8.0, float(end_sec) - float(start_sec))
    cmd = [
        ffmpeg,
        "-y",
        "-ss",
        str(max(0.0, start_sec)),
        "-i",
        video_url,
        "-t",
        str(duration),
        "-vf",
        "scale='min(960,iw)':-2",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "32",
        "-c:a",
        "aac",
        "-b:a",
        "96k",
        str(target),
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True, encoding="utf-8", errors="replace", env=utf8_env())


def prepare_lightweight_teacher_review(
    output_dir: Path,
    teacher_video_url: str,
    transcript_segments: list[dict[str, Any]],
    max_windows: int,
) -> dict[str, Any]:
    if not teacher_video_url:
        return {"status": "skipped_no_teacher_stream", "flags": [], "windows": []}
    detected = detect_teacher_review_windows(transcript_segments, max_windows)
    windows = list(detected["windows"])
    if not windows:
        return {"status": "no_review_flags", "flags": [], "windows": []}
    review_dir = output_dir / "teacher_review"
    review_dir.mkdir(parents=True, exist_ok=True)
    existing = load_existing_teacher_review(review_dir)
    prepared: list[dict[str, Any]] = []
    errors: list[str] = []
    for idx, window in enumerate(windows, start=1):
        clip_path = review_dir / teacher_review_clip_name(str(window["flag"]), idx)
        try:
            extract_teacher_review_clip(
                teacher_video_url,
                clip_path,
                float(window["start_sec"]),
                float(window["end_sec"]),
            )
            clip_status = "prepared"
        except Exception as exc:
            clip_status = "clip_failed"
            errors.append(f"{window['flag']}:{exc}")
        prepared.append(
            {
                **window,
                "start_hms": format_seconds(window["start_sec"]),
                "end_hms": format_seconds(window["end_sec"]),
                "clip_status": clip_status,
                "clip_path": str(clip_path),
            }
        )
    payload = {
        "status": "prepared_with_warnings" if errors else "prepared",
        "flags": detected["flags"],
        "windows": prepared,
        "review_dir": str(review_dir),
        "errors": errors,
        "semantic_review_completed": False,
        "confirmed_items": existing.get("confirmed_items", []),
        "review_questions": build_teacher_review_questions(prepared),
        "confirmed_at": existing.get("confirmed_at", ""),
    }
    write_text(review_dir / "teacher_review.json", json.dumps(payload, ensure_ascii=False, indent=2))
    return payload


def build_markdown_note(
    output_dir: Path,
    metadata: dict[str, Any],
    transcript_segments: list[dict[str, Any]],
    transcript_text: str,
    mode: str = "final",
) -> tuple[str, dict[str, str]]:
    outline_dir = output_dir / "ppt_outline"
    outline_slides = load_outline_slides(outline_dir)
    diagnosis = build_replay_diagnosis(metadata, transcript_segments, outline_slides)
    coverage = diagnosis["coverage"]
    date = lesson_date_from_timestamp(metadata.get("start_at"))
    sub_title = str(metadata.get("sub_title") or metadata.get("sub_id") or "课次").replace("  ", " ").strip()
    title_parts = [date, str(metadata.get("course_title") or "").strip(), sub_title]
    title = " ".join(part for part in title_parts if part)
    if diagnosis["status"] == "waiting_transcript":
        lines = [
            "---",
            'source: "buaa-classroom-summarizer"',
            f'course_title: "{str(metadata.get("course_title") or "").replace("\"", "\\\"")}"',
            f'sub_title: "{sub_title.replace("\"", "\\\"")}"',
            f'sub_id: "{str(metadata.get("sub_id") or "")}"',
            f'date: "{date}"',
            f'preferred_stream: "{str(metadata.get("preferred_stream") or "")}"',
            'replay_diagnosis: "waiting_transcript"',
            "---",
            "",
            f"# {title}",
            "",
            "## 当前判断",
            "",
            "- 平台当前还没有提供可用 transcript，因此不能正式重建课次纪要。",
        ]
        lines.extend(
            [
                "",
                "## 建议处理",
                "",
                "- 先等待更完整的课程材料，再重跑正式重建。",
                "- 如果必须现在推进，应改成人工语义重写，而不是直接接受脚本输出。",
            ]
        )
        return "\n".join(lines), {}
    if diagnosis["status"] == "partial_transcript":
        start_text = format_seconds(coverage.get("last_transcript_sec", 0))
        duration_text = format_seconds(coverage.get("duration_sec", 0))
        lines = [
            "---",
            'source: "buaa-classroom-summarizer"',
            f'course_title: "{str(metadata.get("course_title") or "").replace("\"", "\\\"")}"',
            f'sub_title: "{sub_title.replace("\"", "\\\"")}"',
            f'sub_id: "{str(metadata.get("sub_id") or "")}"',
            f'date: "{date}"',
            f'preferred_stream: "{str(metadata.get("preferred_stream") or "")}"',
            "partial_transcript_diagnostic: true",
            f'transcript_coverage_ratio: {coverage.get("coverage_ratio", 0)}',
            "---",
            "",
            f"# {title}",
            "",
            "## 当前判断",
            "",
            f"- 当前 transcript 只覆盖到约 `{start_text}`，而整节课时长约 `{duration_text}`，覆盖率约 `{coverage.get('coverage_ratio', 0):.0%}`。",
            "- 这类原料条件下不能直接把自动生成结果当成正式纪要，否则很容易出现“前半段靠 transcript，后半段误按 PPT 页标题切段”的问题。",
        ]
        lines.extend(
            [
                "",
                "## 建议处理",
                "",
                "- 先等待更完整的 transcript，再重跑正式重建。",
                "- 如果必须现在推进，应改成人工语义重写，而不是直接接受自动输出。",
                "",
                "## 可追溯原料",
                "",
                "- `metadata.json`：课次元信息、回放流链接、提纲状态。",
                "- `transcript.txt`：当前平台给出的 ASR 文本。",
                "- `transcript.json`：逐段转写及时间戳。",
            ]
        )
        return "\n".join(lines), {}
    sections = build_transcript_fallback_sections(transcript_segments, transcript_text)
    section_contexts: list[tuple[dict[str, Any], list[str]]] = []
    for section in sections:
        transcript_lines = transcript_lines_in_range(
            transcript_segments, float(section.get("start_sec", 0)), float(section.get("end_sec", 0))
        )
        section["role"] = infer_section_role(section, transcript_lines)
        section["display_title"] = display_section_title(section)
        section_contexts.append((section, transcript_lines))
    sections = append_transcript_tail_section_if_needed(sections, transcript_segments)
    section_contexts = []
    for section in sections:
        transcript_lines = transcript_lines_in_range(
            transcript_segments, float(section.get("start_sec", 0)), float(section.get("end_sec", 0))
        )
        section.setdefault("role", infer_section_role(section, transcript_lines))
        section.setdefault("display_title", display_section_title(section))
        section_contexts.append((section, transcript_lines))
    affairs = build_replay_affairs_summary(transcript_text)
    review_items = build_final_review_items(transcript_text, sections, bool(outline_slides))
    summary_coverage = summary_coverage_info(transcript_segments, sections)
    if summary_coverage["insufficient"]:
        start_text = format_seconds(summary_coverage.get("covered_until_sec", 0))
        transcript_end_text = format_seconds(diagnosis["coverage"].get("last_transcript_sec", 0))
        lines = [
            "---",
            'source: "buaa-classroom-summarizer"',
            f'course_title: "{str(metadata.get("course_title") or "").replace("\"", "\\\"")}"',
            f'sub_title: "{sub_title.replace("\"", "\\\"")}"',
            f'sub_id: "{str(metadata.get("sub_id") or "")}"',
            f'date: "{date}"',
            'replay_diagnosis: "needs_review"',
            "summary_coverage_diagnostic: true",
            f'summary_coverage_ratio: {summary_coverage.get("coverage_ratio", 0)}',
            "---",
            "",
            f"# {title}",
            "",
            "## 当前判断",
            "",
            f"- transcript 本身可用，但当前摘要只稳定覆盖到约 `{start_text}`，而 transcript 已覆盖到约 `{transcript_end_text}`。",
            f"- 摘要覆盖率约 `{summary_coverage.get('coverage_ratio', 0):.0%}`，不能把当前 seed note 当成 final note。",
            "- 这时应进入人工/agent 语义重建或继续补强 transcript 理解，而不是直接接受脚本输出。",
            "",
            "## 可追溯原料",
            "",
            "- `metadata.json`：课次元信息和回放流链接。",
            "- `transcript.txt`：完整 transcript 文本。",
            "- `transcript.json`：逐段 transcript 时间戳。",
        ]
        if outline_slides:
            lines.append("- `ppt_outline/`：仅可用于术语、页面标题、公式符号和事务截图的辅助核对。")
        return "\n".join(lines), {}
    semantic_artifacts: dict[str, str] = {}
    section_payloads: list[dict[str, Any]] = []
    transcript_overview = transcript_overview_payload(transcript_segments)

    lines = [
        "---",
        'source: "buaa-classroom-summarizer"',
        f'course_title: "{str(metadata.get("course_title") or "").replace("\"", "\\\"")}"',
        f'sub_title: "{sub_title.replace("\"", "\\\"")}"',
        f'sub_id: "{str(metadata.get("sub_id") or "")}"',
        f'date: "{date}"',
        f'preferred_stream: "{str(metadata.get("preferred_stream") or "")}"',
        f'has_transcript: {"true" if metadata.get("has_transcript") else "false"}',
        f'has_ppt_outline: {"true" if outline_slides else "false"}',
        f'markdown_note_mode: "{mode}"',
        "---",
        "",
        f"# {title}",
        "",
        "## 元信息",
        "",
        f"- 课程：{metadata.get('course_title', '')}",
        f"- 节次：{sub_title}",
        f"- 日期：{date or '待补充'}",
        f"- 任课教师：{metadata.get('lecturer_name', '') or '待补充'}",
        f"- 教室：{metadata.get('room_name', '') or '待补充'}",
        f"- 默认回放流：{metadata.get('preferred_stream', '') or '待补充'}",
        f"- 回放页：<{metadata.get('source_url', '')}>",
    ]
    if metadata.get("teacher_video_url"):
        lines.append(f"- 教师视频：<{metadata['teacher_video_url']}>")
    if metadata.get("ppt_video_url"):
        lines.append(f"- PPT 视频：<{metadata['ppt_video_url']}>")

    lines.extend(["", "## 本节主线", ""])
    mainline = build_final_mainline(sections)
    if mainline:
        lines.extend(f"- {item}" for item in mainline)
    else:
        lines.append("- 当前转写材料还不足以稳定重建这一节的教学主线。")

    lines.extend(["", "## 内容纪要", ""])
    if section_contexts:
        for section, transcript_lines in section_contexts:
            section_bullets: list[str] = []
            lines.append(f"### {section['display_title']}")
            lines.append("")
            start_text = format_seconds(section.get("start_sec", 0))
            end_text = format_seconds(section.get("end_sec", 0))
            if start_text and end_text and start_text != end_text:
                lines.append(f"时间参考：约 `{start_text}-{end_text}`")
                lines.append("")
            for bullet in render_final_section_bullets(section, transcript_lines):
                section_bullets.append(bullet)
                lines.append(f"- {bullet}")
            lines.append("")
            section_payloads.append(
                {
                    "kind": section.get("kind", ""),
                    "role": section.get("role", "lecture"),
                    "title": section["display_title"],
                    "source_title": section["title"],
                    "start_sec": float(section.get("start_sec", 0)),
                    "end_sec": float(section.get("end_sec", 0)),
                    "time_range": f"{start_text}-{end_text}" if start_text and end_text else start_text or end_text or "",
                    "seed_bullets": section_bullets,
                    "transcript_excerpt": transcript_lines[:6],
                    "sample_lines": list(section.get("sample_lines", [])),
                }
            )
    else:
        lines.extend(["- 当前材料还不足以稳定重建更完整的纪要。", ""])

    lines.extend(["## 课程事务", "", "### 作业", ""])
    lines.extend(f"- {item}" for item in affairs["assignment"])
    lines.extend(["", "### 考试", ""])
    lines.extend(f"- {item}" for item in affairs["exam"])
    lines.extend(["", "### 课程安排", ""])
    lines.extend(f"- {item}" for item in affairs["arrangement"])
    lines.extend(["", "### 通知", ""])
    lines.extend(f"- {item}" for item in affairs["notice"])

    teacher_review = metadata.get("teacher_review_result", {})
    teacher_review_windows = teacher_review.get("windows", []) if isinstance(teacher_review, dict) else []
    lines.extend(["", "## 教师流轻量复核", ""])
    if teacher_review_windows:
        lines.append(
            "- 当前只针对事务信息完成了“风险片段定位 + 教师流短片段准备”，还没有自动把教师流内容二次改写进正文。"
        )
        if teacher_review.get("flags"):
            lines.append(f"- 复核标记：`{', '.join(str(item) for item in teacher_review.get('flags', []))}`")
        lines.append("- 若后续有事务信息被人工或二次流程确认，应在对应条目里明确标注“已通过教师流复核”。")
        confirmed_items = teacher_review.get("confirmed_items", [])
        if confirmed_items:
            lines.append("- 已通过教师流复核的事务结论：")
            for item in confirmed_items:
                lines.append(f"  - {item}")
        review_questions = teacher_review.get("review_questions", [])
        if review_questions:
            lines.append("- 推荐优先确认：")
            for item in review_questions[:3]:
                lines.append(f"  - {item}")
        lines.append("")
        for window in teacher_review_windows:
            lines.append(f"### {window.get('label', window.get('flag', '复核片段'))}")
            lines.append("")
            lines.append(
                f"- 时间：`{window.get('start_hms', '')}-{window.get('end_hms', '')}`"
            )
            lines.append(f"- 命中次数：{window.get('hit_count', 0)}")
            if window.get("excerpts"):
                lines.append(f"- 命中摘录：{'；'.join(window.get('excerpts', [])[:3])}")
            lines.append(f"- 教师流片段：`{window.get('clip_path', '')}`")
            lines.append(f"- 片段状态：{window.get('clip_status', 'unknown')}")
            lines.append("")
    else:
        lines.append("- 当前没有定位到需要用教师流复核的事务片段。")

    lines.extend(["", "## 待核对", ""])
    lines.extend(f"- {item}" for item in review_items)

    lines.extend(
        [
            "",
            "## 可追溯原料",
            "",
            "- `metadata.json`：课次元信息、回放流链接、提纲状态。",
            "- `transcript.txt`：合并后的 ASR 文本。",
            "- `transcript.json`：逐段转写及时间戳。",
        ]
    )
    if mode in {"final-lite", "final-explained"}:
        semantic_packet = {
            "mode": mode,
            "course_title": str(metadata.get("course_title") or ""),
            "lesson_title": title,
            "output_dir": str(output_dir),
            "metadata": {
                "date": date,
                "sub_title": sub_title,
                "sub_id": str(metadata.get("sub_id") or ""),
                "preferred_stream": str(metadata.get("preferred_stream") or ""),
                "source_url": str(metadata.get("source_url") or ""),
                "has_ppt_outline": bool(outline_slides),
                "has_teacher_review": bool(teacher_review_windows),
                "source_profile": diagnosis["source_profile"],
                "section_strategy": diagnosis["section_strategy"],
            },
            "constraints": {
                "transcript_first": True,
                "ppt_assisted": True,
                "keep_uncertainty_explicit": True,
                "allow_short_concept_explanations": mode == "final-explained",
            },
            "transcript_overview": transcript_overview,
            "sections": section_payloads,
            "affairs": affairs,
            "review_items": review_items,
            "teacher_review": {
                "confirmed_items": teacher_review.get("confirmed_items", []) if isinstance(teacher_review, dict) else [],
                "review_questions": teacher_review.get("review_questions", []) if isinstance(teacher_review, dict) else [],
                "windows": teacher_review_windows,
            },
            "replay_diagnosis": diagnosis,
            "references": {
                "metadata": str(output_dir / "metadata.json"),
                "transcript": str(output_dir / "transcript.txt"),
                "ppt_outline": str(output_dir / "ppt_outline" / "ppt_outline.md"),
                "teacher_review": str(output_dir / "teacher_review" / "teacher_review.json"),
            },
        }
        semantic_artifacts = write_semantic_rebuild_artifacts(output_dir, semantic_packet, mode)
        lines.extend(
            [
                f"- `semantic_rebuild/semantic_rebuild_input.json`：语义重建输入包。",
                f"- `semantic_rebuild/semantic_rebuild_prompt.md`：语义重建提示。",
            ]
        )
    if outline_slides:
        lines.append("- `supplementary/`：补充材料目录（如有）。")
    return "\n".join(lines), semantic_artifacts


def export_markdown_note(
    output_dir: Path,
    metadata: dict[str, Any],
    transcript_segments: list[dict[str, Any]],
    transcript_text: str,
    note_path: Path,
    mode: str,
) -> dict[str, Any]:
    body, semantic_artifacts = build_markdown_note(output_dir, metadata, transcript_segments, transcript_text, mode)
    coverage = metadata.get("transcript_coverage", {}) if isinstance(metadata, dict) else {}
    diagnostic = bool(isinstance(coverage, dict) and coverage.get("insufficient"))
    if mode in {"final-lite", "final-explained"}:
        if note_path.exists():
            note_path.unlink()
        return {
            "status": "pending_semantic",
            "note_path": str(note_path),
            "markdown_note_mode": mode,
            "has_semantic_rebuild_packet": bool(semantic_artifacts),
            "semantic_rebuild_input": semantic_artifacts.get("input_path", ""),
            "transcript_coverage": coverage,
        }
    write_text(note_path, body)
    return {
        "status": "partial_transcript_diagnostic" if diagnostic else "ok",
        "note_path": str(note_path),
        "markdown_note_mode": mode,
        "has_semantic_rebuild_packet": bool(semantic_artifacts),
        "semantic_rebuild_input": semantic_artifacts.get("input_path", ""),
        "transcript_coverage": coverage,
    }


def classify_stream(item: dict[str, Any]) -> str:
    stream_type = str(item.get("stream_type") or "").strip()
    title = str(item.get("title") or "").lower()
    if stream_type == "2" or "ppt" in title:
        return "ppt"
    if stream_type == "3" or "教师" in title or "teacher" in title:
        return "teacher"
    return "unknown"


def first_stream_url(candidates: list[dict[str, Any]], stream_kind: str) -> str:
    for item in candidates:
        if item.get("stream_kind") == stream_kind and item.get("path"):
            return str(item["path"])
    return ""


def stream_duration_seconds(candidates: list[dict[str, Any]], stream_kind: str) -> int:
    durations: list[int] = []
    for item in candidates:
        if item.get("stream_kind") != stream_kind:
            continue
        try:
            value = int(float(str(item.get("duration") or "0")))
        except ValueError:
            continue
        if value > 0:
            durations.append(value)
    return max(durations) if durations else 0


def count_keyword_hits(text: str, keywords: list[str]) -> int:
    lowered = text.lower()
    return sum(lowered.count(keyword.lower()) for keyword in keywords)


def infer_auto_preferred_stream(
    teacher_url: str,
    ppt_url: str,
    transcript_text: str,
    video_candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    if teacher_url and not ppt_url:
        return {"stream": "teacher", "reason": "only_teacher_stream_available", "scores": {"teacher": 10, "ppt": 0}}
    if ppt_url and not teacher_url:
        return {"stream": "ppt", "reason": "only_ppt_stream_available", "scores": {"teacher": 0, "ppt": 10}}
    if not teacher_url and not ppt_url:
        return {"stream": "", "reason": "no_replay_stream_available", "scores": {"teacher": 0, "ppt": 0}}

    text = transcript_text or ""
    ppt_surface_keywords = ["ppt", "课件", "幻灯片", "这一页", "下一页", "翻页", "翻到", "屏幕", "投影"]
    ppt_context_keywords = ["案例", "讨论", "课程简介", "导论", "part", "流程", "章节", "目录", "提纲"]
    teacher_board_keywords = ["板书", "黑板", "白板", "写一下", "写出", "证明", "推导", "记号", "公式", "演算"]
    teacher_context_keywords = ["上来讲", "汇报", "展示", "同学讲", "板演", "演示"]

    ppt_surface_hits = count_keyword_hits(text, ppt_surface_keywords)
    teacher_board_hits = count_keyword_hits(text, teacher_board_keywords)
    ppt_context_hits = sum(1 for keyword in ppt_context_keywords if keyword.lower() in text.lower())
    teacher_context_hits = sum(1 for keyword in teacher_context_keywords if keyword.lower() in text.lower())
    ppt_duration = stream_duration_seconds(video_candidates, "ppt")
    teacher_duration = stream_duration_seconds(video_candidates, "teacher")

    ppt_score = ppt_surface_hits * 3 + ppt_context_hits
    teacher_score = teacher_board_hits * 2 + teacher_context_hits
    reasons: list[str] = []
    if ppt_surface_hits:
        reasons.append(f"ppt_surface_keywords={ppt_surface_hits}")
    if ppt_context_hits:
        reasons.append(f"ppt_context_signals={ppt_context_hits}")
    if teacher_board_hits:
        reasons.append(f"teacher_board_keywords={teacher_board_hits}")
    if teacher_context_hits:
        reasons.append(f"teacher_context_signals={teacher_context_hits}")
    if ppt_duration and teacher_duration and abs(ppt_duration - teacher_duration) <= 300:
        reasons.append("dual_stream_durations_are_close")

    if ppt_score >= teacher_score + 2 and (ppt_surface_hits > 0 or ppt_context_hits >= 2):
        return {
            "stream": "ppt",
            "reason": ";".join(reasons) or "ppt_context_signals",
            "scores": {"teacher": teacher_score, "ppt": ppt_score},
        }
    if teacher_score >= ppt_score + 2 and (teacher_board_hits > 0 or teacher_context_hits >= 1):
        return {
            "stream": "teacher",
            "reason": ";".join(reasons) or "teacher_board_signals",
            "scores": {"teacher": teacher_score, "ppt": ppt_score},
        }
    if ppt_context_hits >= 3 and teacher_board_hits <= 1 and teacher_context_hits == 0:
        return {
            "stream": "ppt",
            "reason": ";".join(reasons) or "ppt_context_dominant_without_material_teacher_signals",
            "scores": {"teacher": teacher_score, "ppt": ppt_score},
        }
    return {
        "stream": "teacher",
        "reason": ";".join(reasons) or "auto_fallback_to_teacher",
        "scores": {"teacher": teacher_score, "ppt": ppt_score},
    }


def choose_preferred_stream(
    requested: str,
    teacher_url: str,
    ppt_url: str,
    transcript_text: str,
    video_candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    if requested == "ppt":
        if ppt_url:
            return {"stream": "ppt", "reason": "requested_ppt", "scores": {"teacher": 0, "ppt": 1}}
        if teacher_url:
            return {"stream": "teacher", "reason": "requested_ppt_but_missing_fallback_teacher", "scores": {"teacher": 1, "ppt": 0}}
        return {"stream": "", "reason": "requested_ppt_but_no_stream_available", "scores": {"teacher": 0, "ppt": 0}}
    if requested == "teacher":
        if teacher_url:
            return {"stream": "teacher", "reason": "requested_teacher", "scores": {"teacher": 1, "ppt": 0}}
        if ppt_url:
            return {"stream": "ppt", "reason": "requested_teacher_but_missing_fallback_ppt", "scores": {"teacher": 0, "ppt": 1}}
        return {"stream": "", "reason": "requested_teacher_but_no_stream_available", "scores": {"teacher": 0, "ppt": 0}}
    if teacher_url and ppt_url:
        return {"stream": "teacher", "reason": "auto_prefers_teacher_when_both_streams_exist", "scores": {"teacher": 1, "ppt": 1}}
    if teacher_url:
        return {"stream": "teacher", "reason": "auto_only_teacher_available", "scores": {"teacher": 1, "ppt": 0}}
    if ppt_url:
        return {"stream": "ppt", "reason": "auto_only_ppt_available", "scores": {"teacher": 0, "ppt": 1}}
    return {"stream": "", "reason": "auto_no_stream_available", "scores": {"teacher": 0, "ppt": 0}}


def extract_ppt_outline(output_dir: Path, ppt_video_url: str) -> dict[str, Any]:
    outline_dir = output_dir / "ppt_outline"
    cmd = [
        sys.executable,
        str(PPT_OUTLINE_SCRIPT),
        "--video",
        ppt_video_url,
        "--output-dir",
        str(outline_dir),
    ]
    result = subprocess.run(cmd, check=False, capture_output=True, text=True, encoding="utf-8", errors="replace", env=utf8_env())
    if result.returncode != 0:
        return {
            "status": "failed",
            "outline_dir": str(outline_dir),
            "error": result.stderr.strip() or result.stdout.strip() or f"ppt outline extractor exited with {result.returncode}",
        }
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        payload = {
            "status": "failed",
            "outline_dir": str(outline_dir),
            "error": "ppt outline extractor returned non-JSON output",
            "raw_stdout": result.stdout.strip(),
        }
    else:
        payload["outline_dir"] = str(outline_dir)
    return payload


def main() -> None:
    configure_utf8_stdio()
    args = parse_args()
    params = parse_livingroom_url(args.url)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    session = build_session(
        args.url,
        allow_runtime_auth=args.browser_runtime_auth,
        runtime_profile_dir=Path(args.browser_runtime_profile_dir),
        runtime_login_timeout=args.browser_login_timeout,
        browser_channel=args.browser_channel,
    )

    study_auth = fetch_json(
        session,
        f"https://classroom.msa.buaa.edu.cn/coursesourceapi/course/study-auth/{params['course_id']}/{params['sub_id']}",
    )
    if not study_auth.get("data", {}).get("hasPermission"):
        raise SystemExit("Current browser login does not have access to this course subject")

    sub_info = fetch_json(
        session,
        "https://classroom.msa.buaa.edu.cn/courseapi/v3/portal-home-setting/get-sub-info",
        params={"course_id": params["course_id"], "sub_id": params["sub_id"]},
    )
    sub_data = sub_info["data"]

    recommend = fetch_json(
        session,
        "https://yjapi.msa.buaa.edu.cn/courseapi/v2/recommend/recommend-play-lists",
        params={"sub_id": params["sub_id"], "clear_cache": "true"},
    )
    recommend_items = recommend.get("data", []) or []
    if isinstance(recommend_items, dict):
        recommend_items = recommend_items.get("data", []) or recommend_items.get("list", []) or []
    if not isinstance(recommend_items, list):
        recommend_items = []

    resource_guid = sub_data.get("resource_guid", "")
    transcript_raw: dict[str, Any] = {}
    transcript_segments: list[dict[str, Any]] = []
    transcript_text = ""
    if resource_guid:
        transcript_raw = fetch_json(
            session,
            "https://yjapi.msa.buaa.edu.cn/courseapi/v3/web-socket/search-trans-result",
            params={"sub_id": params["sub_id"], "format": "json", "resource_guid": resource_guid},
        )
        transcript_segments, transcript_text = flatten_transcript(transcript_raw)

    teacher_video_url = (
        sub_data.get("content", {}).get("playback", {}).get("url")
        or sub_data.get("content", {}).get("save_playback", {}).get("contents")
        or ""
    )

    video_candidates = []
    for item in recommend_items:
        if not isinstance(item, dict):
            continue
        video_candidates.append(
            {
                "title": item.get("title"),
                "stream_type": item.get("stream_type"),
                "stream_kind": classify_stream(item),
                "duration": item.get("duration"),
                "path": item.get("path"),
                "thumb": item.get("thumb"),
            }
        )

    if not teacher_video_url:
        teacher_video_url = first_stream_url(video_candidates, "teacher")
    ppt_video_url = first_stream_url(video_candidates, "ppt")
    preferred_stream_decision = choose_preferred_stream(
        args.preferred_stream,
        teacher_video_url,
        ppt_video_url,
        transcript_text,
        video_candidates,
    )
    preferred_stream = str(preferred_stream_decision.get("stream") or "")
    preferred_video_url = teacher_video_url if preferred_stream == "teacher" else ppt_video_url if preferred_stream == "ppt" else ""
    auto_extract_ppt_outline = (
        args.export_markdown_note
        and not args.extract_ppt_outline
        and bool(ppt_video_url)
    )

    metadata = {
        "source_url": args.url,
        "course_id": params["course_id"],
        "sub_id": params["sub_id"],
        "tenant_code": params["tenant_code"],
        "course_title": sub_data.get("course_title"),
        "sub_title": sub_data.get("sub_title"),
        "lecturer_name": sub_data.get("lecturer_name"),
        "room_name": sub_data.get("room_name"),
        "start_at": sub_data.get("start_at"),
        "end_at": sub_data.get("end_at"),
        "duration": sub_data.get("duration"),
        "resource_guid": resource_guid,
        "teacher_video_url": teacher_video_url,
        "ppt_video_url": ppt_video_url,
        "preferred_stream_requested": args.preferred_stream,
        "preferred_stream": preferred_stream,
        "preferred_stream_reason": preferred_stream_decision.get("reason", ""),
        "preferred_stream_scores": preferred_stream_decision.get("scores", {}),
        "preferred_video_url": preferred_video_url,
        "video_candidates": video_candidates,
        "transcript_segment_count": len(transcript_segments),
        "has_transcript": bool(transcript_segments),
        "ppt_outline_requested": bool(args.extract_ppt_outline or auto_extract_ppt_outline),
        "ppt_outline_auto_requested": auto_extract_ppt_outline,
        "ppt_outline_status": "not_requested",
        "ppt_outline_dir": "",
        "markdown_note_requested": args.export_markdown_note,
        "markdown_note_status": "not_requested",
        "markdown_note_path": "",
        "teacher_review_requested": args.lightweight_teacher_review,
        "teacher_review_status": "not_requested",
        "teacher_review_dir": "",
        "teacher_review_flags": [],
    }
    metadata["transcript_coverage"] = transcript_coverage_info(metadata, transcript_segments)

    base_name = sanitize_filename(f"{sub_data.get('course_title') or ''}-{sub_data.get('sub_title') or params['sub_id']}")

    if args.download_video and preferred_video_url:
        stream_suffix = preferred_stream or "preferred"
        download_file(session, preferred_video_url, output_dir / f"{base_name}-{stream_suffix}.mp4")

    if args.download_ppt_video:
        ppt_item = next((item for item in video_candidates if item.get("stream_kind") == "ppt"), None)
        if ppt_item and ppt_item.get("path"):
            download_file(session, ppt_item["path"], output_dir / f"{base_name}-ppt.mp4")

    if args.extract_ppt_outline or auto_extract_ppt_outline:
        if ppt_video_url:
            outline_result = extract_ppt_outline(output_dir, ppt_video_url)
            metadata["ppt_outline_status"] = outline_result.get("status", "failed")
            metadata["ppt_outline_dir"] = outline_result.get("outline_dir", str(output_dir / "ppt_outline"))
            metadata["ppt_outline_result"] = outline_result
        else:
            metadata["ppt_outline_status"] = "skipped_no_ppt_stream"
            metadata["ppt_outline_dir"] = str(output_dir / "ppt_outline")
    else:
        metadata["ppt_outline_status"] = "not_requested"

    if args.lightweight_teacher_review:
        teacher_review_result = prepare_lightweight_teacher_review(
            output_dir,
            teacher_video_url,
            transcript_segments,
            max(1, args.teacher_review_max_windows),
        )
        metadata["teacher_review_status"] = teacher_review_result.get("status", "failed")
        metadata["teacher_review_dir"] = teacher_review_result.get("review_dir", str(output_dir / "teacher_review"))
        metadata["teacher_review_flags"] = teacher_review_result.get("flags", [])
        metadata["teacher_review_result"] = teacher_review_result
    else:
        metadata["teacher_review_status"] = "not_requested"

    metadata["replay_diagnosis"] = build_replay_diagnosis(
        metadata,
        transcript_segments,
        load_outline_slides(output_dir / "ppt_outline"),
    )

    if args.export_markdown_note:
        note_path = Path(args.markdown_note_file) if args.markdown_note_file else output_dir / "lesson_note.md"
        try:
            note_result = export_markdown_note(
                output_dir,
                metadata,
                transcript_segments,
                transcript_text,
                note_path,
                args.markdown_note_mode,
            )
        except Exception as exc:
            note_result = {
                "status": "failed",
                "note_path": str(note_path),
                "error": str(exc),
            }
        metadata["markdown_note_status"] = note_result.get("status", "failed")
        metadata["markdown_note_path"] = note_result.get("note_path", str(note_path))
        metadata["markdown_note_result"] = note_result
    else:
        metadata["markdown_note_status"] = "not_requested"

    (output_dir / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "transcript.json").write_text(
        json.dumps(transcript_segments, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    write_text(output_dir / "transcript.txt", transcript_text)

    print(json.dumps(metadata, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
