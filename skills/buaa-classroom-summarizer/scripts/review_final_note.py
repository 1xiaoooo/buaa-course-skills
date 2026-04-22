#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
from validate_final_note import validate_markdown_text


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def infer_transcript_path(explicit: str, semantic_packet: dict[str, Any]) -> str:
    if explicit:
        return explicit
    references = semantic_packet.get("references", {})
    if isinstance(references, dict):
        transcript = str(references.get("transcript") or "").strip()
        if transcript:
            return transcript
    return ""


def transcript_stats(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False, "char_count": 0, "line_count": 0}
    text = path.read_text(encoding="utf-8", errors="replace")
    return {
        "exists": True,
        "char_count": len(text),
        "line_count": len([line for line in text.splitlines() if line.strip()]),
        "sha256": sha256_file(path),
    }


def build_review_prompt() -> str:
    return "\n".join(
        [
            "# Final Note Reviewer Prompt",
            "",
            "你是独立 reviewer，只负责审查当前课次 Markdown 是否可以成为正式成品，不负责润色或改写正文。",
            "",
            "请读取 `final_note_review_input.json`，并至少打开其中的：",
            "",
            "- `note.path`：当前待审 Markdown。",
            "- `transcript.path`：完整课程转写。",
            "- `semantic_input.path`：语义重建输入包。",
            "",
            "审查原则：",
            "",
            "- 审查必须针对 `note.sha256` 对应的当前文件内容；只要 Markdown 被修改，旧审查结论失效。",
            "- 如果 `hard_gate.passed=false`，不能给 `pass`，必须先修正硬门槛问题。",
            "- 缺少作业、考试、提交方式、成绩占比等信息本身不是失败；只有 transcript 出现相关信号而笔记漏掉、写错或瞎编，才算问题。",
            "- 如果 transcript 显示提前下课、自主做题、课堂展示、讨论或事务课，笔记可以短，但必须忠实反映课堂实际发生了什么。",
            "- 如果 transcript 缺失或明显截断到无法支撑正式稿，应给 `reject` 或 `needs_revision`，不能放行。",
            "- 判断重点是：笔记是否忠实、完整、具体地反映了 transcript 中实际发生的课堂内容。",
            "",
            "必须检查：",
            "",
            "- 覆盖度：是否覆盖主要时间区间，是否只写了前半段却假装完整。",
            "- 语义质量：是否真正重建课程内容，而不是泛化总结或转写切片。",
            "- 课程领域表达：是否按课程类型写出足够具体的对象、论点、公式、案例、系统、政策、实验或任务。",
            "- 课堂事务：如果 transcript 提到作业、考试、截止、提交、占比、阅读要求，笔记是否保留高置信度信息并把不确定项放入 `待核对`。",
            "- 教师强调：如果老师反复强调重点、易错点、考试点、定理、公式、定义或例子，笔记是否捕捉。",
            "- 事实支持：是否存在 transcript 不支持的确定性结论。",
            "- 图谱安全：是否适合进入 Obsidian 正式层和概念图谱，是否会污染概念页。",
            "",
            "请只输出 JSON，不要输出 Markdown 解释：",
            "",
            "```json",
            "{",
            '  "decision": "pass | needs_revision | reject",',
            '  "finalization_allowed": false,',
            '  "reviewed_note_sha256": "",',
            '  "coverage_check": "",',
            '  "semantic_quality_check": "",',
            '  "domain_specific_check": "",',
            '  "affairs_check": "",',
            '  "teacher_emphasis_check": "",',
            '  "unsupported_claims": [],',
            '  "missing_supported_content": [],',
            '  "graph_safety_check": "",',
            '  "required_revisions": [],',
            '  "reason": ""',
            "}",
            "```",
            "",
            "`finalization_allowed` 只能在 `decision=pass`、硬门槛通过、且 `reviewed_note_sha256` 等于输入包里的 `note.sha256` 时为 `true`。",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--note", required=True, help="Current final-note Markdown to review")
    parser.add_argument("--transcript", default="", help="Full transcript path. Defaults to semantic input references.transcript")
    parser.add_argument("--semantic-input", default="", help="semantic_rebuild_input.json path")
    parser.add_argument("--output-dir", default="", help="Review packet directory. Defaults to <note-dir>/final_note_review")
    parser.add_argument("--json", action="store_true", help="Print machine-readable result")
    args = parser.parse_args()

    note_path = Path(args.note).resolve()
    if not note_path.exists():
        raise SystemExit(f"note not found: {note_path}")

    semantic_path = Path(args.semantic_input).resolve() if args.semantic_input else Path()
    semantic_packet = read_json(semantic_path) if args.semantic_input else {}
    transcript_raw = infer_transcript_path(args.transcript, semantic_packet)
    transcript_path = Path(transcript_raw).resolve() if transcript_raw else Path()
    output_dir = Path(args.output_dir).resolve() if args.output_dir else note_path.parent / "final_note_review"
    output_dir.mkdir(parents=True, exist_ok=True)

    note_text = note_path.read_text(encoding="utf-8", errors="replace")
    hard_gate_issues = validate_markdown_text(note_text)
    review_input = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "note": {
            "path": str(note_path),
            "sha256": sha256_file(note_path),
            "char_count": len(note_text),
        },
        "transcript": {
            "path": str(transcript_path) if transcript_raw else "",
            **(transcript_stats(transcript_path) if transcript_raw else {"exists": False, "char_count": 0, "line_count": 0}),
        },
        "semantic_input": {
            "path": str(semantic_path) if args.semantic_input else "",
            "exists": bool(args.semantic_input and semantic_path.exists()),
            "sha256": sha256_file(semantic_path) if args.semantic_input and semantic_path.exists() else "",
        },
        "hard_gate": {
            "passed": not hard_gate_issues,
            "issues": hard_gate_issues,
        },
        "semantic_metadata": {
            "mode": semantic_packet.get("mode", ""),
            "course_title": semantic_packet.get("course_title", semantic_packet.get("course_name", "")),
            "lesson_title": semantic_packet.get("lesson_title", ""),
            "replay_diagnosis": semantic_packet.get("replay_diagnosis", {}),
            "constraints": semantic_packet.get("constraints", {}),
        },
        "finalization_rule": (
            "Only the current note file may be finalized after transcript coverage passes, "
            "hard_gate.passed=true, reviewer decision=pass, finalization_allowed=true, "
            "and reviewed_note_sha256 equals note.sha256. Any edit invalidates this review."
        ),
    }

    input_path = output_dir / "final_note_review_input.json"
    prompt_path = output_dir / "final_note_review_prompt.md"
    input_path.write_text(json.dumps(review_input, ensure_ascii=False, indent=2), encoding="utf-8")
    prompt_path.write_text(build_review_prompt(), encoding="utf-8")

    result = {
        "status": "review_packet_created",
        "review_input": str(input_path),
        "review_prompt": str(prompt_path),
        "hard_gate_passed": not hard_gate_issues,
        "note_sha256": review_input["note"]["sha256"],
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"Review packet: {input_path}")
        print(f"Reviewer prompt: {prompt_path}")
        print(f"Hard gate passed: {str(not hard_gate_issues).lower()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
