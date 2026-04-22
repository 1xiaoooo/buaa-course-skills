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
