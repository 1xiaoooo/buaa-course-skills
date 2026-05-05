#!/usr/bin/env python3
"""
Handle two BUAA classroom entry modes:

1. A single livingroom replay URL -> delegate to extract_buaa_classroom.py
2. A course detail URL -> enumerate all lessons, mark replay-ready lessons,
   and optionally extract each replay-ready lesson in chronological order
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

SCRIPT_DIR = Path(__file__).resolve().parent
EXTRACT_SCRIPT = SCRIPT_DIR / "extract_buaa_classroom.py"
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


def load_extract_module():
    spec = importlib.util.spec_from_file_location("extract_buaa_classroom", EXTRACT_SCRIPT)
    if spec is None or spec.loader is None:
        raise SystemExit(f"Cannot load helper script: {EXTRACT_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("url", help="BUAA livingroom or coursedetail URL")
    parser.add_argument("--output-dir", default="buaa_course_output", help="Directory to write extracted files")
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
        default="",
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
        help="Preferred replay stream when extracting replay-ready lessons",
    )
    parser.add_argument(
        "--extract-ppt-outline",
        action="store_true",
        help="For replay-ready lessons, also extract a page-level PPT outline when a PPT stream exists",
    )
    parser.add_argument(
        "--export-markdown-note",
        action="store_true",
        help="For extracted lessons, also export a standalone Markdown lesson note",
    )
    parser.add_argument(
        "--markdown-note-mode",
        choices=["final", "final-lite", "final-explained"],
        default="final",
        help="Standalone Markdown note mode used together with --export-markdown-note",
    )
    parser.add_argument(
        "--lightweight-teacher-review",
        action="store_true",
        help="For extracted lessons, also prepare lightweight teacher-stream review materials",
    )
    parser.add_argument(
        "--teacher-review-max-windows",
        type=int,
        default=4,
        help="Maximum number of teacher-review windows prepared per lesson",
    )
    parser.add_argument(
        "--snapshot-file",
        default="",
        help="Optional path for the saved course-page snapshot JSON. Defaults to <output-dir>/course_page_snapshot.json",
    )
    parser.add_argument("--student", default="", help="Student account used by the course-detail API")
    parser.add_argument(
        "--only-sub-ids",
        default="",
        help="Comma-separated sub_id list. In course-detail mode, extract only these replay-ready lessons",
    )
    parser.add_argument(
        "--exclude-sub-ids",
        default="",
        help="Comma-separated sub_id list. In course-detail mode, skip these lessons during extraction",
    )
    parser.add_argument(
        "--only-dates",
        default="",
        help="Comma-separated YYYY-MM-DD list. In course-detail mode, extract only lessons on these dates",
    )
    parser.add_argument(
        "--exclude-dates",
        default="",
        help="Comma-separated YYYY-MM-DD list. In course-detail mode, skip lessons on these dates",
    )
    parser.add_argument(
        "--extract-existing",
        action="store_true",
        help="For course-detail mode, extract every replay-ready lesson after enumeration",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip lesson extraction when metadata.json already exists in the target lesson folder",
    )
    parser.add_argument(
        "--check-new-replays",
        action="store_true",
        help="Compare the current course-page snapshot with the previous saved snapshot and report new replay-ready lessons",
    )
    return parser.parse_args()


def parse_url(url: str) -> tuple[str, dict[str, str]]:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    params = {key: values[0] for key, values in query.items() if values}
    return parsed.path.lower(), params


def parse_id_csv(raw: str) -> set[str]:
    return {part.strip() for part in raw.split(",") if part.strip()}


def flatten_subtree(node: Any, out: list[dict[str, Any]]) -> None:
    if isinstance(node, list):
        for item in node:
            flatten_subtree(item, out)
        return
    if isinstance(node, dict):
        if "id" in node and "sub_title" in node and "type" in node:
            out.append(node)
            return
        for value in node.values():
            flatten_subtree(value, out)


def build_lesson_index(course_id: str, tenant_code: str, lessons: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for lesson in lessons:
        begin = int(lesson.get("class_begin") or 0)
        dt = datetime.fromtimestamp(begin) if begin else None
        sub_id = str(lesson.get("id") or "")
        replay_ready = lesson.get("type") == "course_live" and lesson.get("sub_status") == "6" and lesson.get(
            "playback_status"
        ) == "1"
        items.append(
            {
                "sub_id": sub_id,
                "date": dt.strftime("%Y-%m-%d") if dt else "",
                "sub_title": lesson.get("sub_title"),
                "type": lesson.get("type"),
                "class_begin": lesson.get("class_begin"),
                "class_over": lesson.get("class_over"),
                "lecturer_name": lesson.get("lecturer_name"),
                "room_name": lesson.get("room_name"),
                "sub_status": lesson.get("sub_status"),
                "playback_status": lesson.get("playback_status"),
                "show": lesson.get("show"),
                "replay_ready": replay_ready,
                "livingroom_url": (
                    f"https://classroom.msa.buaa.edu.cn/livingroom?course_id={course_id}&sub_id={sub_id}&tenant_code={tenant_code}"
                ),
            }
        )
    items.sort(key=lambda x: int(x["class_begin"] or 0))
    return items


def resolve_snapshot_path(output_dir: Path, snapshot_file: str) -> Path:
    if snapshot_file:
        return Path(snapshot_file)
    return output_dir / "course_page_snapshot.json"


def load_snapshot(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def build_snapshot(course_id: str, tenant_code: str, index: list[dict[str, Any]]) -> dict[str, Any]:
    lessons: list[dict[str, Any]] = []
    for item in index:
        lessons.append(
            {
                "sub_id": item["sub_id"],
                "date": item["date"],
                "sub_title": item["sub_title"],
                "type": item["type"],
                "class_begin": item["class_begin"],
                "class_over": item["class_over"],
                "sub_status": item["sub_status"],
                "playback_status": item["playback_status"],
                "replay_ready": item["replay_ready"],
                "livingroom_url": item["livingroom_url"],
            }
        )
    return {
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "course_id": course_id,
        "tenant_code": tenant_code,
        "total_lessons": len(index),
        "replay_ready_lessons": sum(1 for item in index if item["replay_ready"]),
        "lessons": lessons,
    }


def compare_snapshots(previous: dict[str, Any] | None, current: dict[str, Any]) -> dict[str, Any]:
    if previous is None:
        return {
            "course_id": current.get("course_id", ""),
            "checked_at": current.get("checked_at", ""),
            "previous_snapshot_found": False,
            "previous_checked_at": "",
            "current_replay_ready_lessons": current.get("replay_ready_lessons", 0),
            "new_replay_count": 0,
            "new_replays": [],
        }
    previous_lookup = {
        str(item.get("sub_id") or ""): item for item in previous.get("lessons", []) if item.get("sub_id")
    }
    new_replays: list[dict[str, Any]] = []
    for item in current.get("lessons", []):
        if not item.get("replay_ready"):
            continue
        sub_id = str(item.get("sub_id") or "")
        previous_item = previous_lookup.get(sub_id)
        if previous_item is None or not previous_item.get("replay_ready", False):
            new_replays.append(
                {
                    "sub_id": sub_id,
                    "date": item.get("date", ""),
                    "sub_title": item.get("sub_title"),
                    "livingroom_url": item.get("livingroom_url"),
                    "reason": "new_lesson" if previous_item is None else "replay_became_available",
                }
            )
    return {
        "course_id": current.get("course_id", ""),
        "checked_at": current.get("checked_at", ""),
        "previous_snapshot_found": True,
        "previous_checked_at": previous.get("checked_at", ""),
        "current_replay_ready_lessons": current.get("replay_ready_lessons", 0),
        "new_replay_count": len(new_replays),
        "new_replays": new_replays,
    }


def read_lesson_metadata(lesson_dir: Path) -> dict[str, Any]:
    metadata_path = lesson_dir / "metadata.json"
    if not metadata_path.exists():
        return {}
    try:
        return json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def classify_lesson_extract(lesson_dir: Path) -> dict[str, Any]:
    metadata = read_lesson_metadata(lesson_dir)
    diagnosis = str(metadata.get("replay_diagnosis") or "")
    coverage = metadata.get("transcript_coverage") or {}
    if isinstance(coverage, dict):
        coverage_ratio = coverage.get("coverage_ratio", 0)
    else:
        coverage_ratio = coverage or 0
    try:
        coverage_ratio = float(coverage_ratio or 0)
    except (TypeError, ValueError):
        coverage_ratio = 0.0
    return {
        "status": diagnosis or "unknown",
        "has_transcript": bool(metadata.get("has_transcript")),
        "coverage_ratio": coverage_ratio,
        "metadata_path": str(lesson_dir / "metadata.json"),
    }


def print_course_summary(summary: dict[str, Any]) -> None:
    print(json.dumps(summary, ensure_ascii=False))


def print_extract_progress(sub_id: str, title: str, result: dict[str, Any], prefix: str = "extract") -> None:
    status = str(result.get("status") or "unknown")
    coverage_ratio = result.get("coverage_ratio")
    coverage_text = ""
    if isinstance(coverage_ratio, (int, float)) and coverage_ratio > 0:
        coverage_text = f" coverage={coverage_ratio:.2f}"
    print(f"{prefix} {sub_id} {title} {status}{coverage_text}")


def handle_livingroom(
    url: str,
    output_dir: Path,
    preferred_stream: str,
    extract_ppt_outline: bool,
    export_markdown_note: bool,
    markdown_note_mode: str,
    lightweight_teacher_review: bool,
    teacher_review_max_windows: int,
    browser_runtime_auth: bool,
    browser_runtime_profile_dir: str,
    browser_login_timeout: int,
    browser_channel: str,
) -> int:
    cmd = [sys.executable, str(EXTRACT_SCRIPT), url, "--output-dir", str(output_dir), "--preferred-stream", preferred_stream]
    if extract_ppt_outline:
        cmd.append("--extract-ppt-outline")
    if export_markdown_note:
        cmd.append("--export-markdown-note")
        cmd.extend(["--markdown-note-mode", markdown_note_mode])
    if lightweight_teacher_review:
        cmd.append("--lightweight-teacher-review")
        cmd.extend(["--teacher-review-max-windows", str(teacher_review_max_windows)])
    if browser_runtime_auth:
        cmd.append("--browser-runtime-auth")
    if browser_runtime_profile_dir:
        cmd.extend(["--browser-runtime-profile-dir", browser_runtime_profile_dir])
    if browser_login_timeout:
        cmd.extend(["--browser-login-timeout", str(browser_login_timeout)])
    if browser_channel:
        cmd.extend(["--browser-channel", browser_channel])
    return subprocess.run(cmd, check=False, env=utf8_env()).returncode


def handle_coursedetail(
    url: str,
    params: dict[str, str],
    output_dir: Path,
    snapshot_file: str,
    student: str,
    extract_existing: bool,
    skip_existing: bool,
    only_sub_ids: set[str],
    exclude_sub_ids: set[str],
    only_dates: set[str],
    exclude_dates: set[str],
    check_new_replays: bool,
    preferred_stream: str,
    extract_ppt_outline: bool,
    export_markdown_note: bool,
    markdown_note_mode: str,
    lightweight_teacher_review: bool,
    teacher_review_max_windows: int,
    browser_runtime_auth: bool,
    browser_runtime_profile_dir: str,
    browser_login_timeout: int,
    browser_channel: str,
) -> int:
    extract_mod = load_extract_module()
    course_id = params.get("course_id", "")
    tenant_code = params.get("tenant_code", "21") or "21"
    if not course_id:
        raise SystemExit("Course detail URL must include course_id")

    student = student or params.get("username", "")
    runtime_profile = Path(browser_runtime_profile_dir) if browser_runtime_profile_dir else None
    session = extract_mod.build_session(
        url,
        allow_runtime_auth=browser_runtime_auth,
        runtime_profile_dir=runtime_profile,
        runtime_login_timeout=browser_login_timeout,
        browser_channel=browser_channel,
    )
    payload = extract_mod.fetch_json(
        session,
        "https://yjapi.msa.buaa.edu.cn/courseapi/v3/multi-search/get-course-detail",
        params={"course_id": course_id, "student": student},
    )
    (output_dir / "course_detail.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lessons: list[dict[str, Any]] = []
    flatten_subtree(payload.get("data", {}).get("sub_list", {}), lessons)
    index = build_lesson_index(course_id, tenant_code, lessons)
    (output_dir / "lesson_index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    snapshot_path = resolve_snapshot_path(output_dir, snapshot_file)
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    previous_snapshot = load_snapshot(snapshot_path)
    current_snapshot = build_snapshot(course_id, tenant_code, index)
    snapshot_path.write_text(json.dumps(current_snapshot, ensure_ascii=False, indent=2), encoding="utf-8")

    replay_ready = [item for item in index if item["replay_ready"]]
    summary: dict[str, Any] = {
        "course_id": course_id,
        "course_title": payload.get("data", {}).get("course_name", ""),
        "total_lessons": len(index),
        "replay_ready_lessons": len(replay_ready),
        "snapshot_file": str(snapshot_path),
        "extract_existing_requested": bool(extract_existing),
    }
    if check_new_replays:
        replay_check = compare_snapshots(previous_snapshot, current_snapshot)
        replay_check["snapshot_file"] = str(snapshot_path)
        (output_dir / "new_replay_check.json").write_text(
            json.dumps(replay_check, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        summary["new_replay_check"] = replay_check
    print_course_summary(summary)

    if not extract_existing:
        return 0

    selected = replay_ready
    if only_sub_ids:
        selected = [item for item in selected if item["sub_id"] in only_sub_ids]
    if exclude_sub_ids:
        selected = [item for item in selected if item["sub_id"] not in exclude_sub_ids]
    if only_dates:
        selected = [item for item in selected if item["date"] in only_dates]
    if exclude_dates:
        selected = [item for item in selected if item["date"] not in exclude_dates]

    lessons_dir = output_dir / "lessons"
    lessons_dir.mkdir(parents=True, exist_ok=True)
    extract_status_counter: Counter[str] = Counter()
    extracted_count = 0
    skipped_existing_count = 0
    failed_lessons: list[dict[str, Any]] = []
    selected_sub_ids: list[str] = []
    for item in selected:
        selected_sub_ids.append(str(item["sub_id"]))
        lesson_dir = lessons_dir / item["sub_id"]
        metadata_path = lesson_dir / "metadata.json"
        if skip_existing and metadata_path.exists():
            skipped_existing_count += 1
            result = classify_lesson_extract(lesson_dir)
            extract_status_counter[str(result["status"] or "unknown")] += 1
            print_extract_progress(str(item["sub_id"]), str(item["sub_title"]), result, prefix="skip")
            continue
        lesson_dir.mkdir(parents=True, exist_ok=True)
        cmd = [
            sys.executable,
            str(EXTRACT_SCRIPT),
            item["livingroom_url"],
            "--output-dir",
            str(lesson_dir),
            "--preferred-stream",
            preferred_stream,
        ]
        if extract_ppt_outline:
            cmd.append("--extract-ppt-outline")
        if export_markdown_note:
            cmd.append("--export-markdown-note")
            cmd.extend(["--markdown-note-mode", markdown_note_mode])
        if lightweight_teacher_review:
            cmd.append("--lightweight-teacher-review")
            cmd.extend(["--teacher-review-max-windows", str(teacher_review_max_windows)])
        if browser_runtime_auth:
            cmd.append("--browser-runtime-auth")
        if browser_runtime_profile_dir:
            cmd.extend(["--browser-runtime-profile-dir", browser_runtime_profile_dir])
        if browser_login_timeout:
            cmd.extend(["--browser-login-timeout", str(browser_login_timeout)])
        if browser_channel:
            cmd.extend(["--browser-channel", browser_channel])
        completed = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=utf8_env(),
        )
        if completed.returncode != 0:
            failed_lessons.append(
                {
                    "sub_id": str(item["sub_id"]),
                    "sub_title": str(item["sub_title"]),
                    "returncode": completed.returncode,
                    "stderr": (completed.stderr or completed.stdout or "").strip(),
                }
            )
            print(f"extract {item['sub_id']} {item['sub_title']} failed")
            continue
        extracted_count += 1
        result = classify_lesson_extract(lesson_dir)
        extract_status_counter[str(result["status"] or "unknown")] += 1
        print_extract_progress(str(item["sub_id"]), str(item["sub_title"]), result)
    extract_summary = {
        "course_id": course_id,
        "course_title": payload.get("data", {}).get("course_name", ""),
        "selected_lessons": len(selected),
        "selected_sub_ids": selected_sub_ids,
        "extracted_count": extracted_count,
        "skipped_existing_count": skipped_existing_count,
        "failed_count": len(failed_lessons),
        "status_counts": dict(extract_status_counter),
        "transcript_ready_count": extract_status_counter.get("transcript_only", 0),
        "waiting_transcript_count": extract_status_counter.get("waiting_transcript", 0),
        "partial_transcript_count": extract_status_counter.get("partial_transcript", 0),
        "failed_lessons": failed_lessons,
    }
    (output_dir / "course_extract_summary.json").write_text(
        json.dumps(extract_summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print_course_summary(extract_summary)
    if failed_lessons:
        return 1
    return 0


def main() -> None:
    configure_utf8_stdio()
    args = parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    path, params = parse_url(args.url)
    only_sub_ids = parse_id_csv(args.only_sub_ids)
    exclude_sub_ids = parse_id_csv(args.exclude_sub_ids)
    only_dates = parse_id_csv(args.only_dates)
    exclude_dates = parse_id_csv(args.exclude_dates)

    if path.endswith("/livingroom"):
        raise SystemExit(
            handle_livingroom(
                args.url,
                output_dir,
                args.preferred_stream,
                args.extract_ppt_outline,
                args.export_markdown_note,
                args.markdown_note_mode,
                args.lightweight_teacher_review,
                args.teacher_review_max_windows,
                args.browser_runtime_auth,
                args.browser_runtime_profile_dir,
                args.browser_login_timeout,
                args.browser_channel,
            )
        )
    if path.endswith("/coursedetail"):
        raise SystemExit(
            handle_coursedetail(
                args.url,
                params,
                output_dir,
                args.snapshot_file,
                args.student,
                args.extract_existing,
                args.skip_existing,
                only_sub_ids,
                exclude_sub_ids,
                only_dates,
                exclude_dates,
                args.check_new_replays,
                args.preferred_stream,
                args.extract_ppt_outline,
                args.export_markdown_note,
                args.markdown_note_mode,
                args.lightweight_teacher_review,
                args.teacher_review_max_windows,
                args.browser_runtime_auth,
                args.browser_runtime_profile_dir,
                args.browser_login_timeout,
                args.browser_channel,
            )
        )
    parsed = urlparse(args.url)
    if parsed.netloc.lower() == "spoc.buaa.edu.cn" and "/notice/" in path:
        raise SystemExit(
            "SPoC notice URL is no longer supported in the public workflow. "
            "Please open the replay in the browser first and pass the resulting classroom coursedetail URL instead."
        )
    raise SystemExit("URL must be a BUAA livingroom or coursedetail page")


if __name__ == "__main__":
    main()
