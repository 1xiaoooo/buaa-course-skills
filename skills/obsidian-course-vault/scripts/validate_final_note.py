#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


BANNED_LITERAL_PATTERNS = [
    "代表性表达",
    "转写里比较能代表",
    "课堂讲解与主题推进",
    "整理时建议不要把这一段",
    "转写分段",
    "Transcript主题段",
    "主题候选",
    "seed_bullets",
    "sample_lines",
    "transcript_excerpt",
]

GENERIC_BOILERPLATE_PATTERNS = [
    "这一段以老师连续讲解为主",
    "这里保留时间范围，便于后续语义重建",
    "正式主线应结合整节课程转写和课程上下文来重建",
    "先提出对象，再写公式，最后说明为什么",
]

TIMELINE_MARKER_RE = re.compile(r"时间参考：约\s*`?([^`\n]+?)`?(?:\n|$)")
TIMESTAMP_RANGE_RE = re.compile(r"^\s*(\d{1,2}:\d{2}(?::\d{2})?)\s*[-–]\s*(\d{1,2}:\d{2}(?::\d{2})?)\s*$")
MINIMUM_MAJOR_SECTION_SECONDS = 120


def parse_lesson_timestamp(value: str) -> int | None:
    parts = value.strip().split(":")
    if len(parts) == 2:
        minutes, seconds = parts
        if not minutes.isdigit() or not seconds.isdigit():
            return None
        minute_value = int(minutes)
        second_value = int(seconds)
        if second_value >= 60:
            return None
        return minute_value * 60 + second_value
    if len(parts) == 3:
        hours, minutes, seconds = parts
        if not hours.isdigit() or not minutes.isdigit() or not seconds.isdigit():
            return None
        hour_value = int(hours)
        minute_value = int(minutes)
        second_value = int(seconds)
        if minute_value >= 60 or second_value >= 60:
            return None
        return hour_value * 3600 + minute_value * 60 + second_value
    return None


def validate_timeline_markers(text: str) -> list[str]:
    issues: list[str] = []
    previous_start = -1
    previous_end = -1
    for marker in TIMELINE_MARKER_RE.finditer(text):
        raw_range = marker.group(1).strip()
        range_match = TIMESTAMP_RANGE_RE.match(raw_range)
        if not range_match:
            issues.append(f"timeline marker is not a replay timestamp range: {raw_range}")
            continue
        start = parse_lesson_timestamp(range_match.group(1))
        end = parse_lesson_timestamp(range_match.group(2))
        if start is None or end is None:
            issues.append(f"timeline marker has invalid timestamp fields: {raw_range}")
            continue
        if end <= start:
            issues.append(f"timeline range is not increasing: {raw_range}")
            continue
        if end - start < MINIMUM_MAJOR_SECTION_SECONDS:
            issues.append(
                f"timeline range is too short for a major lesson section: {raw_range}; "
                "use HH:MM:SS after the first hour"
            )
        if previous_start >= 0 and start < previous_start:
            issues.append(f"timeline range moves backward: {raw_range}")
        if previous_end >= 0 and end < previous_end:
            issues.append(f"timeline range end moves backward: {raw_range}")
        previous_start = start
        previous_end = end
    return issues


def validate_markdown_text(text: str) -> list[str]:
    issues: list[str] = []
    for pattern in BANNED_LITERAL_PATTERNS:
        if pattern in text:
            issues.append(f"contains internal artifact marker: {pattern}")
    for pattern in GENERIC_BOILERPLATE_PATTERNS:
        if pattern in text:
            issues.append(f"contains generic seed-note boilerplate: {pattern}")

    short_quote_bullets = re.findall(r"(?m)^\s*-\s*[“\"].{1,24}[”\"]\s*$", text)
    if len(short_quote_bullets) >= 3:
        issues.append("contains multiple short quoted transcript snippets that look like raw ASR evidence")

    headings = re.findall(r"(?m)^#{2,4}\s+(.+)$", text)
    weak_headings = [heading for heading in headings if re.search(r"分段|主题段|推进\s*\d+$", heading)]
    if weak_headings:
        issues.append(f"contains weak generated headings: {', '.join(weak_headings[:3])}")

    generic_sentence_count = sum(text.count(pattern) for pattern in GENERIC_BOILERPLATE_PATTERNS)
    if generic_sentence_count >= 2:
        issues.append("contains repeated generic advice instead of course-specific reconstruction")

    issues.extend(validate_timeline_markers(text))

    return issues


def validate_markdown_file(path: Path) -> list[str]:
    return validate_markdown_text(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("markdown_file")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    path = Path(args.markdown_file)
    issues = validate_markdown_file(path)
    if args.json:
        print(json.dumps({"ok": not issues, "issues": issues}, ensure_ascii=False, indent=2))
    elif issues:
        print("Final note quality gate failed:", file=sys.stderr)
        for issue in issues:
            print(f"- {issue}", file=sys.stderr)
    else:
        print("Final note quality gate passed.")
    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
