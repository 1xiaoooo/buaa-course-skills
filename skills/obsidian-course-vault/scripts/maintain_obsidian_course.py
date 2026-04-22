#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
from validate_final_note import validate_markdown_text

SKILLS_ROOT = SCRIPT_DIR.parent.parent
BUAA_REPLAY_SCRIPT = SKILLS_ROOT / "buaa-classroom-summarizer" / "scripts" / "collect_buaa_course_replays.py"
BUAA_SINGLE_REPLAY_SCRIPT = SKILLS_ROOT / "buaa-classroom-summarizer" / "scripts" / "extract_buaa_classroom.py"


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

PLACEHOLDER_TOKENS = {"{{", "[[概念名]]", "[[课次名]]", "[[课程总览]]"}
PENDING_LESSON_SOURCES = {
    "buaa-replay-waiting-transcript",
    "buaa-replay-partial-transcript",
    "buaa-replay-needs-review",
    "buaa-replay-quality-rejected",
}
GENERATED_REPLAY_SOURCES = {
    "buaa-replay-draft",
    "buaa-replay-rebuild",
    "buaa-replay-needs-review",
    "buaa-replay-waiting-transcript",
    "buaa-replay-partial-transcript",
}
DEFAULT_TRACKER_NAMES = ["章节完成度", "已整理课次", "待回看问题"]
DEFAULT_SYNC_NOTE_NAME = "回放同步"
DEFAULT_BACKLOG_NOTE_NAME = "待整理回放"
SEMANTIC_REBUILD_MODES = {"final-lite", "final-explained"}

OUTLINE_SECTION_LIMIT = 12
GRAPH_CONCEPT_PROMOTION_MIN_LESSONS = 2
GRAPH_HUB_SUGGESTION_MIN_CONCEPTS = 3
GRAPH_HUB_SUGGESTION_MIN_SHARED_LESSONS = 2
REPLAY_METADATA_REFRESH_MAX_AGE_HOURS = 12.0
PARTIAL_TRANSCRIPT_COVERAGE_THRESHOLD = 0.6
OUTLINE_SIGNAL_KEYWORDS = [
    "方法",
    "模型",
    "系统",
    "分析",
    "设计",
    "实验",
    "算法",
    "框架",
    "流程",
    "原理",
    "概念",
    "背景",
    "定理",
    "定义",
    "例",
    "实现",
    "应用",
    "theorem",
    "definition",
    "example",
    "algorithm",
    "system",
    "model",
    "design",
    "analysis",
]
OUTLINE_NOISE_TOKENS = [
    "springer",
    "download",
    "edition",
    "gareth",
    "daniela",
    "trevor",
    "robert",
    "jonathan",
    "islr",
    "islp",
    "introduction to statistical learning",
]
PRESENTATION_KEYWORDS = [
    "汇报",
    "汇报人",
    "展示",
    "我们组",
    "同学",
    "点评",
    "案例分析",
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

@dataclass
class HubInfo:
    file_name: str
    title: str
    source_heading: str
    concepts: list[str]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vault-dir", required=True)
    parser.add_argument("--course-name", required=True)
    parser.add_argument("--course-page-url", default="")
    parser.add_argument("--student", default="")
    parser.add_argument("--replay-output-dir", default="")
    parser.add_argument("--browser-runtime-auth", "--edge-runtime-auth", dest="browser_runtime_auth", action="store_true")
    parser.add_argument("--browser-runtime-profile-dir", "--edge-runtime-profile-dir", dest="browser_runtime_profile_dir", default="")
    parser.add_argument("--browser-login-timeout", "--edge-login-timeout", dest="browser_login_timeout", type=int, default=180)
    parser.add_argument("--browser-channel", choices=["auto", "msedge", "chrome"], default="auto")
    parser.add_argument("--ignore-replay-dates", default="")
    parser.add_argument("--ignore-replay-sub-ids", default="")
    parser.add_argument("--preferred-replay-stream", choices=["", "teacher", "ppt", "auto"], default="")
    parser.add_argument("--draft-replay-sub-ids", default="")
    parser.add_argument("--draft-replay-dates", default="")
    parser.add_argument("--replay-note-mode", choices=["draft", "final", "final-lite", "final-explained"], default="final-explained")
    parser.add_argument("--rebuild-upgraded-replays", action="store_true")
    parser.add_argument("--lightweight-teacher-review", action="store_true")
    parser.add_argument("--teacher-review-max-windows", type=int, default=3)
    parser.add_argument("--rebuild-graph", action="store_true")
    parser.add_argument("--skip-buaa-sync", action="store_true")
    parser.add_argument("--skip-noise-scan", action="store_true")
    parser.add_argument("--skip-frontmatter", action="store_true")
    parser.add_argument("--skip-trackers", action="store_true")
    return parser.parse_args()


def sanitize_name(name: str) -> str:
    return re.sub(r'[<>:"/\\\\|?*]+', "_", name.strip()) or "课程"


def parse_csv(raw: str) -> list[str]:
    return [part.strip() for part in raw.split(",") if part.strip()]


def read_json(path: Path) -> Any:
    return json.loads(read_text(path))


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.rstrip() + "\n", encoding="utf-8")


def metadata_age_hours(path: Path) -> float:
    return max(0.0, (datetime.now().timestamp() - path.stat().st_mtime) / 3600.0)


def metadata_has_ppt_stream(metadata: dict[str, Any]) -> bool:
    if metadata.get("ppt_video_url"):
        return True
    return any(
        str(candidate.get("stream_type") or "") == "2" or "ppt" in str(candidate.get("title") or "").lower()
        for candidate in metadata.get("video_candidates", [])
    )


def metadata_needs_refresh(metadata_path: Path, metadata: dict[str, Any], *, require_ppt_outline: bool) -> list[str]:
    reasons: list[str] = []
    age_hours = metadata_age_hours(metadata_path)
    raw_transcript_coverage = metadata.get("transcript_coverage") or 0.0
    if isinstance(raw_transcript_coverage, dict):
        raw_transcript_coverage = raw_transcript_coverage.get("coverage_ratio") or 0.0
    try:
        transcript_coverage = float(raw_transcript_coverage or 0.0)
    except (TypeError, ValueError):
        transcript_coverage = 0.0
    has_transcript = bool(metadata.get("has_transcript"))
    if age_hours < REPLAY_METADATA_REFRESH_MAX_AGE_HOURS:
        return reasons
    if not has_transcript:
        reasons.append("stale_metadata_recheck_for_transcript")
    elif 0 < transcript_coverage < PARTIAL_TRANSCRIPT_COVERAGE_THRESHOLD:
        reasons.append("stale_metadata_recheck_for_partial_transcript")
    return unique_keep_order(reasons)


def extract_frontmatter_and_body(text: str) -> tuple[dict[str, Any], str]:
    if text.startswith("---\n"):
        match = re.match(r"^---\n(.*?)\n---\n?", text, re.DOTALL)
        if match:
            frontmatter = yaml.safe_load(match.group(1)) or {}
            body = text[match.end() :]
            if not isinstance(frontmatter, dict):
                frontmatter = {}
            return frontmatter, body
    return {}, text


def dump_frontmatter(data: dict[str, Any], body: str) -> str:
    yaml_text = yaml.safe_dump(data, allow_unicode=True, sort_keys=False).strip()
    return f"---\n{yaml_text}\n---\n\n{body.lstrip()}"


def upsert_frontmatter(path: Path, updates: dict[str, Any], body: str | None = None) -> None:
    frontmatter, current_body = extract_frontmatter_and_body(read_text(path))
    merged = {**frontmatter, **updates}
    write_text(path, dump_frontmatter(merged, current_body if body is None else body))


def note_title_from_path(path: Path) -> str:
    return path.stem


def wiki_links(text: str) -> list[str]:
    links: list[str] = []
    for raw in re.findall(r"\[\[([^\]]+)\]\]", text):
        target = raw.split("|", 1)[0]
        target = target.rsplit("/", 1)[-1].strip()
        if target:
            links.append(target)
    return links


def unique_keep_order(items: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def load_existing_course_concepts(vault_dir: Path, course_name: str) -> list[str]:
    concept_dir = vault_dir / "02-Concepts" / course_name
    if not concept_dir.exists():
        return []
    concepts: list[str] = []
    for path in sorted(concept_dir.glob("*.md")):
        stem = path.stem.strip()
        if not stem or "图谱" in stem:
            continue
        concepts.append(stem)
    return unique_keep_order(concepts)


def write_minimal_concept_stub(course_name: str, concept_dir: Path, concept: str, lesson_title: str) -> None:
    path = concept_dir / f"{concept}.md"
    if path.exists():
        return
    body = f"""# {concept}

## 本节语境

- 待语义重建：当前仅把“{concept}”作为课程主线候选概念，后续需要结合 transcript、最近课次和课程上下文确认它的定义、作用和边界。

## 前置概念

- 待补充

## 推导到 / 关联到

- 待补充

## 典型例子

- 待补充

## 出现在哪些课次

- [[{lesson_title}]]
"""
    write_text(path, body)
    upsert_frontmatter(
        path,
        {
            "type": "concept",
            "course": course_name,
            "title": concept,
            "lesson_refs": [lesson_title],
        },
    )


def concept_wiki_link(course_name: str, concept: str) -> str:
    return f"[[02-Concepts/{course_name}/{concept}]]"


def render_concept_label(course_name: str, concept: str, existing_concepts: set[str]) -> str:
    return concept_wiki_link(course_name, concept) if concept in existing_concepts else concept


def linkify_concept_mentions(text: str, course_name: str, concepts: list[str]) -> str:
    if not text.strip() or not concepts:
        return text
    placeholders: dict[str, str] = {}
    linked = text
    for index, concept in enumerate(sorted(unique_keep_order(concepts), key=len, reverse=True)):
        if concept not in linked:
            continue
        token = f"__CONCEPT_{index}__"
        placeholders[token] = concept_wiki_link(course_name, concept)
        linked = linked.replace(concept, token)
    for token, replacement in placeholders.items():
        linked = linked.replace(token, replacement)
    return linked


def listify(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def split_sections(body: str) -> list[tuple[int, str, list[str]]]:
    sections: list[tuple[int, str, list[str]]] = []
    current_level = 0
    current_title = ""
    current_lines: list[str] = []
    for line in body.splitlines():
        heading = re.match(r"^(#{2,3})\s+(.*)$", line)
        if heading:
            if current_title:
                sections.append((current_level, current_title, current_lines))
            current_level = len(heading.group(1))
            current_title = heading.group(2).strip()
            current_lines = []
            continue
        if current_title:
            current_lines.append(line)
    if current_title:
        sections.append((current_level, current_title, current_lines))
    return sections


def get_section_lines(body: str, heading_names: list[str]) -> list[str]:
    matches: list[str] = []
    for _, title, lines in split_sections(body):
        if title in heading_names:
            matches.extend(lines)
    return matches


def get_section_text(body: str, heading_names: list[str]) -> str:
    return "\n".join(get_section_lines(body, heading_names)).strip()


def get_bullet_items(body: str, heading_names: list[str]) -> list[str]:
    items: list[str] = []
    for line in get_section_lines(body, heading_names):
        stripped = line.strip()
        if stripped.startswith("- "):
            item = stripped[2:].strip()
            if item:
                items.append(item)
    return items


def first_nonempty_bullets(body: str, heading_names: list[str]) -> list[str]:
    return [item for item in get_bullet_items(body, heading_names) if item not in {"", "-"}]


def remove_sections(body: str, heading_names: list[str]) -> str:
    lines = body.splitlines()
    kept: list[str] = []
    skip = False
    for line in lines:
        heading = re.match(r"^##\s+(.*)$", line)
        if heading:
            skip = heading.group(1).strip() in heading_names
        if not skip:
            kept.append(line)
    cleaned = "\n".join(kept)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned + "\n" if cleaned else ""


def lesson_date_from_name(name: str) -> str:
    match = re.search(r"(\d{4}-\d{2}-\d{2})", name)
    return match.group(1) if match else ""


def file_name_safe(text: str) -> str:
    return re.sub(r'[<>:"/\\\\|?*]+', "_", text).strip() or "章节图谱"


def strip_leading_number(title: str) -> str:
    return re.sub(r"^\d+\s*[-.、]?\s*", "", title).strip()


def infer_hub_file_name(index: int, heading: str) -> str:
    base = strip_leading_number(heading)
    if not base.endswith("图谱"):
        base = f"{base}图谱"
    return f"{index:02d}-{file_name_safe(base)}.md"


def infer_hub_intro(heading: str, concepts: list[str]) -> str:
    concept_line = "、".join(concepts[:4]) if concepts else "本章核心概念"
    return f"- 本部分围绕 {concept_line} 展开。"


def infer_hubs_from_graph_entry(course_name: str, concept_dir: Path, rebuild_graph: bool) -> tuple[Path, list[HubInfo]]:
    candidates = [
        concept_dir / f"{course_name}概念图谱.md",
        concept_dir / "概念图谱.md",
    ]
    main_graph = next((path for path in candidates if path.exists()), None)
    if main_graph is None:
        existing = sorted(concept_dir.glob("*概念图谱*.md"))
        main_graph = existing[0] if existing else concept_dir / f"{course_name}概念图谱.md"
    existing_hubs = sorted(
        path for path in concept_dir.glob("[0-9][0-9]-*图谱.md") if path.name != main_graph.name
    )
    if existing_hubs and not rebuild_graph:
        hubs: list[HubInfo] = []
        for path in existing_hubs:
            text = read_text(path)
            _, body = extract_frontmatter_and_body(text)
            concepts = unique_keep_order(wiki_links(body))
            hubs.append(HubInfo(path.name, path.stem, path.stem, concepts))
        return main_graph, hubs

    if not main_graph.exists():
        return main_graph, []

    _, body = extract_frontmatter_and_body(read_text(main_graph))
    sections = split_sections(body)
    hubs: list[HubInfo] = []
    idx = 1
    for level, title, lines in sections:
        if level != 2:
            continue
        if title in {"浏览建议", "课程总主线", "课程主干入口", "例子层"}:
            continue
        concepts = unique_keep_order(wiki_links("\n".join(lines)))
        if not concepts:
            continue
        file_name = infer_hub_file_name(idx, title)
        hubs.append(HubInfo(file_name, Path(file_name).stem, title, concepts))
        idx += 1
    return main_graph, hubs


def write_hub_pages(course_name: str, concept_dir: Path, hubs: list[HubInfo]) -> None:
    for hub in hubs:
        concept_bullets = "\n".join(f"- [[{concept}]]" for concept in hub.concepts)
        body = f"""# {hub.title}

## 本部分回答什么问题

{infer_hub_intro(hub.source_heading, hub.concepts)}

## 核心概念

{concept_bullets}

## 回看顺序建议

- 先看本页列出的核心概念，再顺着概念页里的“前置概念”和“推导到 / 关联到”继续展开。
"""
        path = concept_dir / hub.file_name
        write_text(path, body)
        upsert_frontmatter(
            path,
            {
                "type": "concept_hub",
                "course": course_name,
                "title": hub.title,
                "concepts": hub.concepts,
            },
        )


def write_graph_entry(course_name: str, main_graph: Path, hubs: list[HubInfo]) -> None:
    hub_links = "\n".join(f"- [[{Path(hub.file_name).stem}]]" for hub in hubs) or "- "
    body = f"""# {course_name}概念图谱

## 课程主干入口

{hub_links}

## 浏览建议

- 在 Obsidian Graph 里过滤：`path:"02-Concepts/{course_name}"`
- 日常浏览时隐藏未解析链接与孤立节点，先看主干网络。
- 如果某个概念还是孤点，优先补它和章节枢纽页、前置概念、后续概念之间的连接。
"""
    write_text(main_graph, body)
    upsert_frontmatter(main_graph, {"type": "course_graph", "course": course_name, "hub_count": len(hubs)})


def concept_note_paths(concept_dir: Path) -> list[Path]:
    return sorted(
        path
        for path in concept_dir.glob("*.md")
        if "图谱" not in path.stem or path.name.endswith("概念图谱.md")
    )


def lesson_note_paths(course_dir: Path) -> list[Path]:
    lesson_dir = course_dir / "课次"
    return sorted(path for path in lesson_dir.glob("*.md"))


def hub_concept_map(hubs: list[HubInfo]) -> dict[str, list[str]]:
    mapping: dict[str, list[str]] = {}
    for hub in hubs:
        for concept in hub.concepts:
            mapping.setdefault(concept, []).append(hub.title)
    return mapping


def normalize_concept_frontmatter(course_name: str, concept_dir: Path, hubs: list[HubInfo]) -> list[dict[str, Any]]:
    chapter_map = hub_concept_map(hubs)
    concept_summaries: list[dict[str, Any]] = []
    for path in sorted(path for path in concept_dir.glob("*.md") if path.name.endswith(".md")):
        text = read_text(path)
        frontmatter, body = extract_frontmatter_and_body(text)
        note_type = "concept_hub" if path.name in {hub.file_name for hub in hubs} else "concept"
        if path.name.endswith("概念图谱.md"):
            note_type = "course_graph"
        title = note_title_from_path(path)
        prerequisites = unique_keep_order(wiki_links("\n".join(get_section_lines(body, ["前置概念"]))))
        related = unique_keep_order(wiki_links("\n".join(get_section_lines(body, ["推导到 / 关联到", "核心概念"]))))
        contrasts = unique_keep_order(wiki_links("\n".join(get_section_lines(body, ["易混概念"]))))
        examples = unique_keep_order(wiki_links("\n".join(get_section_lines(body, ["典型例子"]))))
        lesson_links = unique_keep_order(wiki_links("\n".join(get_section_lines(body, ["出现在哪些课次"]))))
        if not lesson_links:
            lesson_links = listify(frontmatter.get("lesson_refs"))
        first_seen = min((lesson_date_from_name(name) for name in lesson_links if lesson_date_from_name(name)), default="")
        if not first_seen:
            first_seen = str(frontmatter.get("first_seen") or "")
        updates = {
            "type": note_type,
            "course": course_name,
            "title": title,
        }
        cleaned_body = body
        if note_type == "concept":
            updates["chapter"] = chapter_map.get(title, [])
            updates["first_seen"] = first_seen
            updates["prerequisites"] = prerequisites
            updates["related"] = related
            updates["contrasts"] = contrasts
            updates["examples"] = examples
            updates["lesson_refs"] = lesson_links
            cleaned_body = remove_sections(body, ["所属课程", "所在章节", "首次出现", "出现在哪些课次"])
        elif note_type == "concept_hub":
            updates["concepts"] = unique_keep_order(wiki_links(body))
        upsert_frontmatter(path, updates, cleaned_body)
        concept_summaries.append(
            {
                "path": str(path),
                "title": title,
                "type": note_type,
                "chapters": chapter_map.get(title, []),
                "lesson_refs": lesson_links,
            }
        )
    return concept_summaries


def normalize_lesson_frontmatter(course_name: str, course_dir: Path) -> list[dict[str, Any]]:
    lesson_summaries: list[dict[str, Any]] = []
    for path in lesson_note_paths(course_dir):
        text = read_text(path)
        frontmatter, body = extract_frontmatter_and_body(text)
        existing_source = str(frontmatter.get("source") or "")
        semantic_rebuild_completed = bool(frontmatter.get("semantic_rebuild_completed"))
        quality_issues = validate_markdown_text(body)
        title = note_title_from_path(path)
        date = lesson_date_from_name(title)
        concepts = unique_keep_order(wiki_links("\n".join(get_section_lines(body, ["本节提到的概念", "主题"]))))
        if not concepts:
            concepts = listify(frontmatter.get("concepts"))
        review_items = [
            item
            for item in first_nonempty_bullets(body, ["待核对"])
            if item not in {"", " -", "[[课次名]]"} and "概念名" not in item
        ]
        updates = {
            "type": "lesson",
            "course": course_name,
            "title": title,
            "date": date,
            "concepts": concepts,
            "review_items": review_items,
        }
        if quality_issues and (semantic_rebuild_completed or existing_source == "buaa-replay-semantic-rebuild"):
            updates["source"] = "buaa-replay-quality-rejected"
            updates["semantic_rebuild_status"] = "rejected"
            updates["quality_gate_status"] = "failed"
            updates["quality_gate_issues"] = quality_issues
        elif semantic_rebuild_completed or existing_source == "buaa-replay-semantic-rebuild":
            updates["source"] = "buaa-replay-semantic-rebuild"
            updates["semantic_rebuild_status"] = "completed"
        elif bool(frontmatter.get("has_semantic_rebuild_packet")):
            updates["semantic_rebuild_status"] = "required"
        elif "BUAA 课程回放重建纪要" in body:
            updates["source"] = "buaa-replay-rebuild"
        upsert_frontmatter(path, updates)
        source = str(updates.get("source") or existing_source or "")
        if source in PENDING_LESSON_SOURCES or updates.get("semantic_rebuild_status") == "required":
            continue
        lesson_summaries.append(
            {
                "path": str(path),
                "title": title,
                "date": date,
                "concepts": concepts,
                "concept_count": len(concepts),
                "review_items": review_items,
            }
        )
    return lesson_summaries


def replace_or_append_section(body: str, heading: str, new_lines: list[str]) -> str:
    lines = body.splitlines()
    start = None
    end = None
    for idx, line in enumerate(lines):
        if line.strip() == heading:
            start = idx
            break
    if start is None:
        if body and not body.endswith("\n"):
            body += "\n"
        suffix = "\n".join([heading, "", *new_lines]).rstrip()
        return body.rstrip() + "\n\n" + suffix + "\n"
    end = len(lines)
    for idx in range(start + 1, len(lines)):
        if re.match(r"^##\s+", lines[idx]):
            end = idx
            break
    replacement = [heading, "", *new_lines]
    merged = lines[:start] + replacement + lines[end:]
    return "\n".join(merged).rstrip() + "\n"


def update_course_overview(
    course_name: str,
    course_dir: Path,
    hubs: list[HubInfo],
    tracker_names: list[str],
    sync_note_name: str,
    backlog_note_name: str,
) -> None:
    overview_path = course_dir / "00-课程总览.md"
    if overview_path.exists():
        text = read_text(overview_path)
        frontmatter, body = extract_frontmatter_and_body(text)
    else:
        frontmatter, body = {}, f"# {course_name}\n"
    chapter_lines = [f"- 图谱入口：[[02-Concepts/{course_name}/{course_name}概念图谱]]"]
    chapter_lines.extend(f"- {hub.title}：[[02-Concepts/{course_name}/{Path(hub.file_name).stem}]]" for hub in hubs)
    tracker_lines = [f"- [[{name}]]" for name in tracker_names]
    tracker_lines.append(f"- [[{sync_note_name}]]")
    tracker_lines.append(f"- [[{backlog_note_name}]]")
    body = replace_or_append_section(body, "## 章节地图", chapter_lines)
    body = replace_or_append_section(body, "## 课程事务", tracker_lines + ["- [[03-Admin/作业总表]]", "- [[03-Admin/考试与通知]]"])
    write_text(overview_path, dump_frontmatter({**frontmatter, "type": "course_overview", "course": course_name}, body))


def update_course_trackers(
    course_name: str,
    course_dir: Path,
    concept_dir: Path,
    hubs: list[HubInfo],
    lesson_summaries: list[dict[str, Any]],
    concept_summaries: list[dict[str, Any]],
) -> list[str]:
    tracker_names = ["章节完成度", "已整理课次", "待回看问题"]
    chapter_rows = ["| 章节 | 概念数 | 已接入概念页 | 相关课次 | 备注 |", "| --- | --- | --- | --- | --- |"]
    concept_lookup = {item["title"]: item for item in concept_summaries if item["type"] == "concept"}
    lesson_by_title = {item["title"]: item for item in lesson_summaries}
    for hub in hubs:
        lesson_refs: set[str] = set()
        concept_count = 0
        existing_count = 0
        for concept_name in hub.concepts:
            concept_count += 1
            info = concept_lookup.get(concept_name)
            if info:
                existing_count += 1
                for lesson_ref in info.get("lesson_refs", []):
                    lesson_refs.add(lesson_ref)
        chapter_rows.append(
            f"| [[02-Concepts/{course_name}/{Path(hub.file_name).stem}]] | {concept_count} | {existing_count} | {len(lesson_refs)} | |"
        )
    chapter_body = "# 章节完成度\n\n" + "\n".join(chapter_rows)
    write_text(course_dir / "章节完成度.md", chapter_body)

    lesson_rows = ["| 日期 | 课次 | 概念数 | 待核对项 |", "| --- | --- | --- | --- |"]
    for lesson in sorted(lesson_summaries, key=lambda item: item["date"] or item["title"]):
        lesson_rows.append(
            f"| {lesson['date']} | [[课次/{lesson['title']}]] | {lesson['concept_count']} | {len(lesson['review_items'])} |"
        )
    lesson_body = "# 已整理课次\n\n" + "\n".join(lesson_rows)
    write_text(course_dir / "已整理课次.md", lesson_body)

    review_lines = ["# 待回看问题", ""]
    total_review_items = 0
    for lesson in sorted(lesson_summaries, key=lambda item: item["date"] or item["title"]):
        if not lesson["review_items"]:
            continue
        review_lines.append(f"## [[课次/{lesson['title']}]]")
        review_lines.append("")
        for item in lesson["review_items"]:
            review_lines.append(f"- {item}")
            total_review_items += 1
        review_lines.append("")
    if total_review_items == 0:
        review_lines.extend(["- 当前没有汇总出的待回看问题。", ""])
    write_text(course_dir / "待回看问题.md", "\n".join(review_lines))

    return tracker_names


def build_graph_growth_candidates(
    lesson_summaries: list[dict[str, Any]],
    concept_summaries: list[dict[str, Any]],
    hubs: list[HubInfo],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    existing_concepts = {item["title"] for item in concept_summaries if item["type"] == "concept"}
    hub_map = hub_concept_map(hubs)
    concept_lessons: dict[str, set[str]] = {}
    for lesson in lesson_summaries:
        lesson_title = str(lesson.get("title") or "")
        for concept in lesson.get("concepts", []):
            concept_lessons.setdefault(concept, set()).add(lesson_title)

    candidate_concepts: list[dict[str, Any]] = []
    for concept, lessons in sorted(concept_lessons.items(), key=lambda item: (-len(item[1]), item[0])):
        if concept in existing_concepts:
            continue
        if len(lessons) < GRAPH_CONCEPT_PROMOTION_MIN_LESSONS:
            continue
        candidate_concepts.append(
            {
                "concept": concept,
                "lesson_count": len(lessons),
                "lessons": sorted(lessons),
            }
        )

    pair_counts: dict[tuple[str, str], set[str]] = {}
    for lesson in lesson_summaries:
        concepts = sorted(
            concept
            for concept in lesson.get("concepts", [])
            if concept in existing_concepts and not hub_map.get(concept)
        )
        for idx, left in enumerate(concepts):
            for right in concepts[idx + 1 :]:
                pair_counts.setdefault((left, right), set()).add(str(lesson.get("title") or ""))

    graph = {concept: set() for concept in existing_concepts if not hub_map.get(concept)}
    for (left, right), lessons in pair_counts.items():
        if len(lessons) < GRAPH_HUB_SUGGESTION_MIN_SHARED_LESSONS:
            continue
        graph.setdefault(left, set()).add(right)
        graph.setdefault(right, set()).add(left)

    hub_suggestions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for concept in sorted(graph):
        if concept in seen:
            continue
        stack = [concept]
        component: set[str] = set()
        while stack:
            current = stack.pop()
            if current in component:
                continue
            component.add(current)
            stack.extend(graph.get(current, set()) - component)
        seen.update(component)
        if len(component) < GRAPH_HUB_SUGGESTION_MIN_CONCEPTS:
            continue
        shared_lessons = sorted(
            lesson["title"]
            for lesson in lesson_summaries
            if len(component.intersection(set(lesson.get("concepts", [])))) >= GRAPH_HUB_SUGGESTION_MIN_CONCEPTS
        )
        if len(shared_lessons) < GRAPH_HUB_SUGGESTION_MIN_SHARED_LESSONS:
            continue
        hub_suggestions.append(
            {
                "concepts": sorted(component),
                "shared_lessons": shared_lessons,
            }
        )
    return candidate_concepts, hub_suggestions


def cleanup_graph_growth_notes(course_dir: Path) -> None:
    for path in [course_dir / "图谱生长规则.md", course_dir / "概念生长建议.md"]:
        if path.exists():
            path.unlink()


def build_graph_growth_context(
    lesson_summaries: list[dict[str, Any]],
    concept_summaries: list[dict[str, Any]],
    hubs: list[HubInfo],
) -> dict[str, Any]:
    rules_lines = [
        "# 图谱生长规则",
        "",
        "## 新概念页何时升格",
        "",
        f"- 一个概念至少在 `{GRAPH_CONCEPT_PROMOTION_MIN_LESSONS}` 节已整理课次里进入 `主题` 或 `本节提到的概念`，才考虑新建概念页。",
        "- 如果只是课堂举例、延伸阅读或一次性提到，不升格成概念页。",
        "- 已经有概念页的条目，优先补它和旧图谱的连接，而不是重复建页。",
        "",
        "## 新 hub 何时增加",
        "",
        f"- 至少 `{GRAPH_HUB_SUGGESTION_MIN_CONCEPTS}` 个已建概念页在至少 `{GRAPH_HUB_SUGGESTION_MIN_SHARED_LESSONS}` 节课中反复共同出现，才考虑新开一个章节 hub。",
        "- 如果这些概念已经被现有 hub 吸收，就继续补链，不再重复拆 hub。",
        "- 只有当一组概念开始形成稳定的方法链或章节主线时，才值得长新 hub。",
        "",
        "## 不自动升格的情况",
        "",
        "- 只在一节课出现一次的术语。",
        "- 纯 PPT 页面标题、临时例子、教材页码、延伸阅读或课堂闲谈。",
        "- 还没有进入课程主线、也没有和旧图谱形成连接的孤立词语。",
    ]

    concept_candidates, hub_suggestions = build_graph_growth_candidates(lesson_summaries, concept_summaries, hubs)
    suggestion_lines = [
        "# 概念生长建议",
        "",
        "## 待升格概念",
        "",
    ]
    if concept_candidates:
        for item in concept_candidates:
            suggestion_lines.append(
                f"- `{item['concept']}`：已在 {item['lesson_count']} 节已整理课次中出现，可考虑新建概念页。"
            )
            suggestion_lines.append(f"  涉及课次：{ '；'.join(item['lessons']) }")
    else:
        suggestion_lines.append("- 当前没有达到升格阈值但尚未建页的新概念。")
    suggestion_lines.extend(["", "## 待增加 hub", ""])
    if hub_suggestions:
        for item in hub_suggestions:
            suggestion_lines.append(f"- 候选概念组：{ '、'.join(item['concepts']) }")
            suggestion_lines.append(f"  共同出现课次：{ '；'.join(item['shared_lessons']) }")
    else:
        suggestion_lines.append("- 当前没有达到新 hub 阈值的稳定概念组。")
    return {
        "rules_excerpt": rules_lines,
        "suggestions_excerpt": suggestion_lines,
        "concept_candidates": concept_candidates,
        "hub_suggestions": hub_suggestions,
        "thresholds": {
            "concept_promotion_min_lessons": GRAPH_CONCEPT_PROMOTION_MIN_LESSONS,
            "hub_suggestion_min_concepts": GRAPH_HUB_SUGGESTION_MIN_CONCEPTS,
            "hub_suggestion_min_shared_lessons": GRAPH_HUB_SUGGESTION_MIN_SHARED_LESSONS,
        },
    }


def summarize_hubs(hubs: list[HubInfo]) -> list[dict[str, Any]]:
    return [
        {
            "title": hub.title,
            "file_name": hub.file_name,
            "concept_count": len(hub.concepts),
            "concepts": hub.concepts[:8],
        }
        for hub in hubs
    ]


def load_recent_lesson_context(course_dir: Path, current_title: str, limit: int = 4) -> list[dict[str, Any]]:
    lessons: list[dict[str, Any]] = []
    for path in lesson_note_paths(course_dir):
        title = note_title_from_path(path)
        if title == current_title:
            continue
        text = read_text(path)
        frontmatter, body = extract_frontmatter_and_body(text)
        source = str(frontmatter.get("source") or "")
        if source in PENDING_LESSON_SOURCES:
            continue
        mainline = first_nonempty_bullets(body, ["本节主线"])
        concepts = listify(frontmatter.get("concepts"))
        lessons.append(
            {
                "title": title,
                "date": str(frontmatter.get("date") or lesson_date_from_name(title)),
                "source": source,
                "mainline": mainline[:4],
                "concepts": concepts[:10],
            }
        )
    lessons.sort(key=lambda item: item["date"] or item["title"])
    return lessons[-limit:]


def load_graph_growth_context(course_name: str, course_dir: Path, hubs: list[HubInfo]) -> dict[str, Any]:
    concept_dir = course_dir.parent.parent / "02-Concepts" / course_name
    lesson_summaries = normalize_lesson_frontmatter(course_name, course_dir)
    concept_summaries = normalize_concept_frontmatter(course_name, concept_dir, hubs)
    return build_graph_growth_context(lesson_summaries, concept_summaries, hubs)


def build_course_alignment_context(
    course_name: str,
    course_dir: Path,
    hubs: list[HubInfo],
    current_title: str,
    current_concepts: list[str],
) -> dict[str, Any]:
    recent_lessons = load_recent_lesson_context(course_dir, current_title)
    hub_titles = [hub.title for hub in hubs]
    hub_concepts = {concept for hub in hubs for concept in hub.concepts}
    matched = [concept for concept in current_concepts if concept in hub_concepts]
    unmatched = [concept for concept in current_concepts if concept not in hub_concepts]
    return {
        "course_name": course_name,
        "hub_titles": hub_titles,
        "recent_lessons": recent_lessons,
        "current_concepts_in_graph": matched,
        "current_concepts_outside_graph": unmatched,
        "course_alignment_checklist": [
            "判断当前课次主线是否与最近 2-4 节课的主题连续，而不是跳到别的课程域。",
            "判断当前概念候选是否大多落在现有 hub 或现有概念网里；若大多不在图谱中，需警惕串课或 OCR 带偏。",
            "如果出现阅读延伸、教材提示或课堂闲谈，只能作为次要信息，不要让它们主导正式课次纪要。",
        ],
    }


def load_course_config(config_path: Path) -> dict[str, Any]:
    if config_path.exists():
        return json.loads(read_text(config_path))
    return {}


def save_course_config(config_path: Path, config: dict[str, Any]) -> None:
    write_text(config_path, json.dumps(config, ensure_ascii=False, indent=2))


def build_course_config(
    course_name: str,
    config_path: Path,
    hubs: list[HubInfo],
    args: argparse.Namespace,
) -> dict[str, Any]:
    existing = load_course_config(config_path)
    replay_output_dir = args.replay_output_dir or existing.get("replay_output_dir", "")
    config = {
        "course_name": course_name,
        "course_page_url": args.course_page_url or existing.get("course_page_url", ""),
        "student": args.student or existing.get("student", ""),
        "replay_output_dir": replay_output_dir,
        "browser_runtime_auth": bool(
            args.browser_runtime_auth
            or existing.get("browser_runtime_auth", False)
            or existing.get("edge_runtime_auth", False)
        ),
        "browser_runtime_profile_dir": (
            args.browser_runtime_profile_dir
            or existing.get("browser_runtime_profile_dir", "")
            or existing.get("edge_runtime_profile_dir", "")
        ),
        "browser_login_timeout": (
            args.browser_login_timeout
            or existing.get("browser_login_timeout", 180)
            or existing.get("edge_login_timeout", 180)
        ),
        "browser_channel": args.browser_channel or existing.get("browser_channel", "auto"),
        "preferred_replay_stream": args.preferred_replay_stream or existing.get("preferred_replay_stream", "teacher"),
        "lightweight_teacher_review": bool(args.lightweight_teacher_review),
        "teacher_review_max_windows": args.teacher_review_max_windows,
        "ignored_replay_dates": parse_csv(args.ignore_replay_dates) or existing.get("ignored_replay_dates", []),
        "ignored_replay_sub_ids": parse_csv(args.ignore_replay_sub_ids) or existing.get("ignored_replay_sub_ids", []),
        "chapter_hubs": [{"file": hub.file_name, "title": hub.title, "concepts": hub.concepts} for hub in hubs],
    }
    persisted = {key: value for key, value in config.items() if key not in {"lightweight_teacher_review", "teacher_review_max_windows"}}
    save_course_config(config_path, persisted)
    return config


def ensure_semantic_graph_bootstrap(
    course_name: str,
    course_dir: Path,
    config: dict[str, Any],
    _lesson_title: str,
    _concept_candidates: list[str],
    mode: str,
) -> tuple[list[HubInfo], list[str], dict[str, Any]]:
    vault_dir = course_dir.parent.parent
    concept_dir = vault_dir / "02-Concepts" / course_name
    existing_concepts = load_existing_course_concepts(vault_dir, course_name)
    hubs = [
        HubInfo(
            file_name=str(item.get("file", "")),
            title=str(item.get("title", "")),
            source_heading=str(item.get("title", "")),
            concepts=listify(item.get("concepts")),
        )
        for item in config.get("chapter_hubs", [])
        if isinstance(item, dict)
    ]
    bootstrap_info = {
        "created": False,
        "reason": "",
        "hub_title": "",
        "hub_file": "",
        "concepts": [],
    }
    if mode not in SEMANTIC_REBUILD_MODES:
        bootstrap_info["reason"] = "non_semantic_mode"
        return hubs, existing_concepts, bootstrap_info
    if hubs or existing_concepts:
        bootstrap_info["reason"] = "graph_already_exists"
        return hubs, existing_concepts, bootstrap_info

    bootstrap_info["reason"] = "bootstrap_disabled"
    return hubs, existing_concepts, bootstrap_info


def normalize_sub_title(sub_title: str) -> str:
    text = re.sub(r"\s+", "", str(sub_title or ""))
    text = re.sub(r"^(第\d+周星期\d+)(第.+节)$", r"\1 \2", text)
    return text


def lesson_path_for_replay_item(course_dir: Path, item: dict[str, Any]) -> Path:
    lesson_title = f"{item.get('date', '')} {normalize_sub_title(item.get('sub_title', ''))}".strip()
    return course_dir / "课次" / f"{lesson_title}.md"


def source_upgrade_reasons(course_dir: Path, output_dir: Path, item: dict[str, Any]) -> list[str]:
    lesson_path = lesson_path_for_replay_item(course_dir, item)
    if not lesson_path.exists():
        return []
    frontmatter, _ = extract_frontmatter_and_body(read_text(lesson_path))
    source = str(frontmatter.get("source") or "")
    if not source or source in PENDING_LESSON_SOURCES:
        return []
    lesson_dir = output_dir / "lessons" / str(item["sub_id"])
    metadata_path = lesson_dir / "metadata.json"
    if not metadata_path.exists():
        return []
    metadata = read_json(metadata_path)
    reasons: list[str] = []
    has_ppt_outline_now = (lesson_dir / "ppt_outline" / "ppt_outline.json").exists()
    note_has_ppt_outline = bool(frontmatter.get("has_ppt_outline"))
    if has_ppt_outline_now and not note_has_ppt_outline:
        reasons.append("当前回放原料已新增 PPT 流与页级提纲，现有笔记仍基于旧原料。")
    if has_ppt_outline_now and str(frontmatter.get("draft_basis") or "") == "transcript_only":
            reasons.append("现有笔记最初按仅转写模式重建，现已可以按转写 + PPT 复查。")
    if "has_transcript" in frontmatter and bool(metadata.get("has_transcript")) and not bool(frontmatter.get("has_transcript")):
            reasons.append("平台现已提供课程转写，现有笔记创建时还没有可用转写。")
    return unique_keep_order(reasons)


def semantic_rebuild_pending_lessons(course_dir: Path, replay_output_dir: Path | None = None) -> list[dict[str, Any]]:
    pending: list[dict[str, Any]] = []
    for path in lesson_note_paths(course_dir):
        frontmatter, _ = extract_frontmatter_and_body(read_text(path))
        if not bool(frontmatter.get("has_semantic_rebuild_packet")):
            continue
        if bool(frontmatter.get("semantic_rebuild_completed")):
            continue
        pending.append(
            {
                "path": str(path),
                "title": str(frontmatter.get("title") or note_title_from_path(path)),
                "date": str(frontmatter.get("date") or lesson_date_from_name(path.stem)),
                "sub_id": str(frontmatter.get("replay_sub_id") or ""),
                "mode": str(frontmatter.get("rebuild_mode") or ""),
            }
        )
    if replay_output_dir:
        lessons_dir = replay_output_dir / "lessons"
        if lessons_dir.exists():
            for packet_path in lessons_dir.glob("*/semantic_rebuild/semantic_rebuild_input.json"):
                try:
                    packet = read_json(packet_path)
                except Exception:
                    continue
                lesson_note_path = Path(str(packet.get("lesson_note_path") or ""))
                if lesson_note_path.exists():
                    frontmatter, _ = extract_frontmatter_and_body(read_text(lesson_note_path))
                    if bool(frontmatter.get("semantic_rebuild_completed")) or str(frontmatter.get("source") or "") == "buaa-replay-semantic-rebuild":
                        continue
                pending.append(
                    {
                        "path": str(lesson_note_path),
                        "title": str(packet.get("lesson_title") or lesson_note_path.stem or packet_path.parent.parent.name),
                        "date": str((packet.get("metadata") or {}).get("date") or ""),
                        "sub_id": str((packet.get("metadata") or {}).get("replay_sub_id") or packet_path.parent.parent.name),
                        "mode": str(packet.get("mode") or ""),
                    }
                )
    deduped: dict[str, dict[str, Any]] = {}
    for item in pending:
        key = str(item.get("sub_id") or item.get("title") or item.get("path") or "")
        if key and key not in deduped:
            deduped[key] = item
    return sorted(deduped.values(), key=lambda item: item["date"] or item["title"])


def auto_rebuildable_review_sub_ids(course_dir: Path, review_candidates: list[dict[str, Any]]) -> list[str]:
    sub_ids: list[str] = []
    for item in review_candidates:
        lesson_path = lesson_path_for_replay_item(course_dir, item)
        if not lesson_path.exists():
            continue
        frontmatter, _ = extract_frontmatter_and_body(read_text(lesson_path))
        source = str(frontmatter.get("source") or "")
        if source in GENERATED_REPLAY_SOURCES:
            sub_ids.append(str(item.get("sub_id") or ""))
    return unique_keep_order([item for item in sub_ids if item])


def format_seconds(total_seconds: float | int | str) -> str:
    try:
        value = int(float(total_seconds))
    except (TypeError, ValueError):
        return ""
    hours, remainder = divmod(value, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def slugify_outline_line(text: str) -> str:
    cleaned = re.sub(r"[^\w\u4e00-\u9fff]+", "", text or "", flags=re.UNICODE).lower()
    return cleaned


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
    if re.fullmatch(r"[0-9:./,\- ]{2,}", line):
        return ""
    if len(line) <= 1:
        return ""
    if not re.search(r"[\u4e00-\u9fff]", line):
        return ""
    if len(line) <= 2 and not re.search(r"[\u4e00-\u9fffA-Za-z]{2,}", line):
        return ""
    return line


def outline_text_signal(text: str) -> int:
    raw = str(text or "").strip()
    if not raw:
        return -10
    lowered = raw.lower()
    cjk_count = len(re.findall(r"[\u4e00-\u9fff]", raw))
    ascii_word_count = len(re.findall(r"[A-Za-z]{3,}", raw))
    score = 0
    if any(keyword in raw or keyword in lowered for keyword in OUTLINE_SIGNAL_KEYWORDS):
        score += 3
    if 2 <= cjk_count <= 20:
        score += 2
    elif cjk_count == 1:
        score -= 1
    if 2 <= len(raw) <= 32:
        score += 1
    if ascii_word_count >= 4 and cjk_count < 2:
        score -= 3
    if re.search(r"[作欧仁玫供职商易美学]{3,}", raw) and not any(
        keyword in raw or keyword in lowered for keyword in OUTLINE_SIGNAL_KEYWORDS
    ):
        score -= 3
    if any(token in lowered for token in OUTLINE_NOISE_TOKENS):
        score -= 4
    return score


def choose_outline_heading(lines: list[str]) -> str:
    if not lines:
        return ""
    joined = " ".join(lines)
    if any(token in joined for token in ["教材", "习题", "第三版教材"]):
        return "教材与作业提示"
    if any(token in joined for token in ["课程简介", "课程定位", "课程安排", "学习方式"]):
        return "课程定位与方法入口"
    ranked = sorted(
        lines,
        key=lambda item: (
            outline_text_signal(item),
            len(re.findall(r"[\u4e00-\u9fff]", item)),
            -len(item),
        ),
        reverse=True,
    )
    return ranked[0]


def compact_outline_groups(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compacted: list[dict[str, Any]] = []
    for group in groups:
        heading = str(group.get("heading") or "")
        signal = outline_text_signal(heading)
        duration = max(0.0, float(group.get("end_sec", 0)) - float(group.get("start_sec", 0)))
        points = [point for point in group.get("points", []) if outline_text_signal(point) > 0]
        if signal <= 0 and len(points) < 2:
            continue
        if compacted:
            previous = compacted[-1]
            previous_signal = outline_text_signal(str(previous.get("heading") or ""))
            previous_duration = max(
                0.0,
                float(previous.get("end_sec", 0)) - float(previous.get("start_sec", 0)),
            )
            if (
                signal <= 1 and duration <= 25
            ) or (
                previous_signal <= 1 and previous_duration <= 25
            ) or (
                duration <= 180 and section_kind_from_heading(heading) == "generic"
            ):
                previous["end_sec"] = max(float(previous.get("end_sec", 0)), float(group.get("end_sec", 0)))
                previous["slides"] = list(previous.get("slides", [])) + list(group.get("slides", []))
                previous["points"] = unique_keep_order(list(previous.get("points", [])) + [heading] + points)
                continue
        compacted.append(
            {
                **group,
                "heading": heading,
                "points": points,
            }
        )
    while len(compacted) > OUTLINE_SECTION_LIMIT:
        merge_idx = min(
            range(1, len(compacted)),
            key=lambda idx: (
                max(
                    0.0,
                    float(compacted[idx].get("end_sec", 0)) - float(compacted[idx].get("start_sec", 0)),
                ),
                outline_text_signal(str(compacted[idx].get("heading") or "")),
            ),
        )
        previous = compacted[merge_idx - 1]
        current = compacted[merge_idx]
        previous["end_sec"] = max(float(previous.get("end_sec", 0)), float(current.get("end_sec", 0)))
        previous["slides"] = list(previous.get("slides", [])) + list(current.get("slides", []))
        previous["points"] = unique_keep_order(
            list(previous.get("points", [])) + [current.get("heading", "")] + list(current.get("points", []))
        )
        compacted.pop(merge_idx)
    return compacted


def load_outline_slides(outline_dir: Path) -> list[dict[str, Any]]:
    outline_json = outline_dir / "ppt_outline.json"
    if not outline_json.exists():
        return []
    data = read_json(outline_json)
    return data if isinstance(data, list) else []


def build_outline_groups(slides: list[dict[str, Any]]) -> list[dict[str, Any]]:
    line_counts: dict[str, int] = {}
    prepared: list[tuple[dict[str, Any], list[str]]] = []
    for slide in slides:
        raw_lines = slide.get("ocr_lines", []) or []
        cleaned_lines = unique_keep_order(
            [
                clean_outline_line(line)
                for line in raw_lines
                if clean_outline_line(line) and outline_text_signal(clean_outline_line(line)) > 0
            ]
        )
        if not cleaned_lines:
            preview = clean_outline_line(slide.get("ocr_preview", ""))
            if preview and outline_text_signal(preview) > 0:
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
        heading = choose_outline_heading(cleaned_lines)
        key = slugify_outline_line(heading)
        points = [line for line in cleaned_lines if line != heading]
        if key and key == previous_key and groups:
            group = groups[-1]
            group["end_sec"] = slide.get("timestamp_sec", group["end_sec"])
            group["slides"].append(slide.get("file_name", ""))
            group["points"] = unique_keep_order(group["points"] + points[:4])
            continue
        groups.append(
            {
                "heading": heading,
                "start_sec": slide.get("timestamp_sec", 0),
                "end_sec": slide.get("timestamp_sec", 0),
                "points": points[:4],
                "slides": [slide.get("file_name", "")],
            }
        )
        previous_key = key
    for idx, group in enumerate(groups[:-1]):
        next_start = groups[idx + 1]["start_sec"]
        if next_start and next_start > group["start_sec"]:
            group["end_sec"] = next_start
    return compact_outline_groups(groups)


def collect_transcript_hits(transcript_path: Path, keywords: list[str], limit: int = 3) -> list[str]:
    if not transcript_path.exists():
        return []
    hits: list[str] = []
    for line in read_text(transcript_path).splitlines():
        text = re.sub(r"\s+", " ", line).strip()
        if not text:
            continue
        if not any(keyword in text for keyword in keywords):
            continue
        if len(text) > 90:
            continue
        hits.append(text)
        if len(hits) >= limit:
            break
    return unique_keep_order(hits)


def load_transcript_segments(lesson_dir: Path) -> list[dict[str, Any]]:
    path = lesson_dir / "transcript.json"
    if not path.exists():
        return []
    data = read_json(path)
    return data if isinstance(data, list) else []


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
    first_sec = 0.0
    last_sec = 0.0
    for item in transcript_segments:
        try:
            begin_sec = float(item.get("begin_sec") or 0)
            end_sec = float(item.get("end_sec") or begin_sec)
        except (TypeError, ValueError):
            begin_sec = 0.0
            end_sec = 0.0
        if first_sec == 0.0 and begin_sec > 0:
            first_sec = begin_sec
        last_sec = max(last_sec, begin_sec, end_sec)
    return {
        "segment_count": len(transcript_segments),
        "first_sec": round(first_sec, 2),
        "last_sec": round(last_sec, 2),
        "evidence_policy": "Read transcript.txt for semantic reconstruction; do not quote packet evidence into final notes.",
    }


def transcript_mentions(lines: list[str], keywords: list[str]) -> bool:
    joined = " ".join(lines)
    return any(keyword in joined for keyword in keywords)


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
    if any(token in heading for token in ["课程简介", "学习方式", "为什么要学", "课程定位", "方法入口", "课程安排"]):
        return "course_design"
    if any(token in heading for token in ["简介", "导论", "是什么"]):
        return "intro"
    if any(token in heading for token in ["教材", "作业提示", "阅读延伸", "参考资料", "延伸阅读"]):
        return "reading_extension"
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
        "reading_extension": "教材与阅读延伸",
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


def build_transcript_fallback_sections(transcript_segments: list[dict[str, Any]], transcript_path: Path) -> list[dict[str, Any]]:
    text = read_text(transcript_path) if transcript_path.exists() else ""
    if not text.strip():
        text = "\n".join(str(item.get("text", "")) for item in transcript_segments)
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
            }
        )
    return sections


def build_final_mainline(sections: list[dict[str, Any]]) -> list[str]:
    if sections and all(section.get("kind") == "transcript_topic" for section in sections):
        return [
            "当前为仅转写课次，正式主线应依赖语义重建，不应直接把脚本候选短语当成最终课程主线。",
            "请优先依据时间分段、整节课程转写、最近课次和课程概念网，重建老师这一节真正推进的问题、方法或结论。",
        ]
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
        elif kind == "reading_extension":
            lines.append("把教材、作业和阅读路径接进来，作为本节主线之外的延伸材料。")
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
    if kind == "reading_extension":
        bullets.append("这一段更像教材、作业和延伸阅读的提示，而不是本节主干知识的继续推导。")
        if transcript_mentions(transcript_lines, ["教材", "作业", "习题"]):
            bullets.append("老师把教材章节和作业需要用到的内容点了出来，提醒大家课后要结合书本继续消化。")
        if transcript_mentions(transcript_lines, ["延伸", "阅读", "参考", "背景材料"]):
            bullets.append("这一段也包含了进一步阅读或背景材料提示，更适合作为课后延伸，而不是正文主线。")
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


def build_replay_affairs_summary(transcript_path: Path) -> dict[str, list[str]]:
    if not transcript_path.exists():
        return {
            "assignment": ["当前未从转写中识别出稳定的作业信息。"],
            "exam": ["当前未从转写中识别出稳定的考试安排。"],
            "arrangement": ["当前未从转写中识别出稳定的课程安排信息。"],
            "notice": ["当前未从转写中识别出稳定的课程通知。"],
        }
    text = read_text(transcript_path)
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


def load_teacher_review_payload(lesson_dir: Path) -> dict[str, Any]:
    review_path = lesson_dir / "teacher_review" / "teacher_review.json"
    if not review_path.exists():
        return {}
    data = read_json(review_path)
    return data if isinstance(data, dict) else {}


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


def build_final_review_items(transcript_path: Path, sections: list[dict[str, Any]]) -> list[str]:
    text = read_text(transcript_path) if transcript_path.exists() else ""
    review_items: list[str] = []
    if "大作业" in text or any(keyword in text for keyword in ["考试", "考核"]):
        review_items.append("课程考核细节的转写噪声较大，目前只能较确定地看出“有一项大作业”，其余比例和形式需回看确认。")
    if any(section["kind"] in {"foundations", "inference"} for section in sections):
        review_items.append("这节课的主线和段落边界应始终以课程转写为准；如后续需要核对术语、公式符号或事务截图，再把 PPT 当作局部辅助参考。")
    if any(keyword in text for keyword in ["讨论", "案例", "交流"]):
        review_items.append("课堂上提到会安排讨论与案例分析，但“具体在几节课里展开”这一节奏仍需后续课次或教师流进一步确认。")
    if not review_items:
        review_items.append("当前未发现必须立刻复核的大段内容，但若后续整理概念页，仍建议抽样回看教师流。")
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
            "请基于 `semantic_rebuild_input.json` 重写课次纪要，遵守以下约束：",
            "",
            "- 课程转写永远是唯一主来源；PPT 只作辅助校正。",
            "- PPT 只允许补术语拼写、书名或页面标题、公式符号、课程事务类截图信息。",
            "- 不要让 PPT 决定 section 边界、主线、概念提取或课次完成状态。",
            "- 不要把 OCR 碎句或 ASR 噪声原样抄进正文。",
            "- 主线和内容纪要要像人类学生整理后的课程纪要，而不是关键词拼接。",
            "- 你是在写面向学生的完成稿，不是在写 seed note、诊断稿或给后续整理者看的说明。",
            "- `sections` 只是时间窗，不是正文提纲；不要照抄其中的标题、证据策略或内部字段。",
            "- 必须先读取 `references.transcript` 指向的完整转写，再写最终纪要。",
            "- 最终 Markdown 不得出现 `代表性表达`、`转写里比较能代表`、`转写分段`、`课堂讲解与主题推进`、`整理时建议` 等中间产物痕迹。",
            "- 每个主要时间段都要说明真实教学动作：定义、模型、论证、证明、例子、比较、案例讨论、政策解释、教师点评、作业、考试安排或课堂事务。",
            "- 必须捕捉高价值课堂信号：考试、作业、截止时间、提交格式、成绩占比、阅读要求、老师反复强调的重点、易错点、公式、定理、定义和例子。",
            "- 如果老师明确说某内容重要、可能考试、容易混淆、经常出错或课后需要复习，应保留在正文或 `待核对` 中。",
            "- 如果证据不足，把事项放进 `待核对`；不要把弱证据改写成确定结论。",
            "- 先判断课程领域，再选择表达方式：数学/统计重建对象、假设、公式、定理、证明思路和例子；工科/计算机重建系统、算法、约束、步骤、实验和权衡；文科/社科/思政重建概念、论点、背景、材料、案例和教师评价重点；实验/项目课重建任务、工具、交付物、操作步骤和评分要求。",
            "- 如果 `has_ppt_outline=false`，不要套用通用课程模板标题；应根据 `sections`、`transcript_overview`、各段课程转写片段和课程上下文自行归纳 3 到 6 个真实主题。",
            "- `内容纪要` 的每个分段都必须保留时间轴。优先沿用 packet 里已有的 `time_range`；如果时间只适合写成粗粒度区间，也要保留“时间参考：约 `MM:SS-MM:SS`”或同等清晰的时间标记。",
            "- 已有概念页必须在 `主题`、`本节主线 / 内容纪要`、`本节提到的概念` 中作为 wiki link 出现。",
            "- 不要预设这门课属于统计、数学、工科或文科中的任何一类，先做课程域判断，再写正文。",
            concept_rule,
            "- 事务信息只写高置信度结论；不确定项放进 `待核对`。",
            "- 不要补出课程转写 / PPT / 教师流片段都没有支持的新结论。",
            "- 先阅读 packet 里的 `course_alignment` 和 `graph_growth`，先判断课程域是否对齐，再决定正文怎么写。",
            "- 对概念升格和 hub 增长只给建议，不要在没有上下文支持时擅自扩张图谱。",
        ]
    )


def write_semantic_rebuild_artifacts(lesson_dir: Path, packet: dict[str, Any], mode: str) -> dict[str, str]:
    semantic_dir = lesson_dir / "semantic_rebuild"
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


def ensure_replay_extracts(
    config: dict[str, Any],
    sub_ids: list[str],
    dates: list[str],
    *,
    require_ppt_outline: bool = False,
) -> dict[str, Any]:
    course_page_url = config.get("course_page_url", "")
    replay_output_dir = config.get("replay_output_dir", "")
    if not replay_output_dir:
        return {"status": "skipped", "reason": "missing_replay_output_dir"}
    if not sub_ids and not dates:
        return {"status": "skipped", "reason": "no_targets"}
    if not course_page_url:
        return {"status": "skipped", "reason": "missing_course_page_url"}
    output_dir = Path(replay_output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    lesson_index_path = output_dir / "lesson_index.json"
    if lesson_index_path.exists():
        lesson_index = read_json(lesson_index_path)
        selected = []
        for item in lesson_index:
            if sub_ids and item.get("sub_id") in sub_ids:
                selected.append(item)
                continue
            if dates and item.get("date") in dates:
                selected.append(item)
        selected = [item for item in selected if item.get("replay_ready")]
        if selected:
            fully_cached = True
            missing_reasons: dict[str, list[str]] = {}
            for item in selected:
                lesson_dir = output_dir / "lessons" / str(item["sub_id"])
                metadata_path = lesson_dir / "metadata.json"
                reasons: list[str] = []
                if not metadata_path.exists():
                    reasons.append("missing_metadata")
                else:
                    metadata = read_json(metadata_path)
                    reasons.extend(metadata_needs_refresh(metadata_path, metadata, require_ppt_outline=require_ppt_outline))
                    if config.get("lightweight_teacher_review") and not (
                        lesson_dir / "teacher_review" / "teacher_review.json"
                    ).exists():
                        reasons.append("missing_teacher_review")
                if reasons:
                    fully_cached = False
                    missing_reasons[str(item["sub_id"])] = reasons
            if fully_cached:
                return {
                    "status": "cached",
                    "returncode": 0,
                    "stdout": "reused_existing_replay_extracts",
                    "stderr": "",
                }
            outputs: list[str] = []
            errors: list[str] = []
            for item in selected:
                sub_id = str(item["sub_id"])
                reasons = missing_reasons.get(sub_id, [])
                if not reasons:
                    outputs.append(f"reused_existing_replay_extract {sub_id}")
                    continue
                lesson_dir = output_dir / "lessons" / sub_id
                lesson_dir.mkdir(parents=True, exist_ok=True)
                cmd = [
                    sys.executable,
                    str(BUAA_SINGLE_REPLAY_SCRIPT),
                    str(item["livingroom_url"]),
                    "--output-dir",
                    str(lesson_dir),
                    "--preferred-stream",
                    config.get("preferred_replay_stream", "teacher"),
                ]
                if config.get("lightweight_teacher_review"):
                    cmd.append("--lightweight-teacher-review")
                    cmd.extend(["--teacher-review-max-windows", str(config.get("teacher_review_max_windows", 3))])
                if config.get("browser_runtime_auth"):
                    cmd.append("--browser-runtime-auth")
                if config.get("browser_runtime_profile_dir"):
                    cmd.extend(["--browser-runtime-profile-dir", str(config["browser_runtime_profile_dir"])])
                if config.get("browser_login_timeout"):
                    cmd.extend(["--browser-login-timeout", str(config["browser_login_timeout"])])
                if config.get("browser_channel"):
                    cmd.extend(["--browser-channel", str(config["browser_channel"])])
                completed = subprocess.run(
                    cmd,
                    check=False,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env=utf8_env(),
                )
                outputs.append(
                    f"refreshed {sub_id} ({', '.join(reasons)}): {completed.stdout.strip() or 'ok'}"
                )
                if completed.returncode != 0:
                    errors.append(
                        f"{sub_id}: {completed.stderr.strip() or completed.stdout.strip() or 'extract_failed'}"
                    )
            return {
                "status": "ok" if not errors else "error",
                "returncode": 0 if not errors else 1,
                "stdout": "\n".join(outputs).strip(),
                "stderr": "\n".join(errors).strip(),
            }
    cmd = [
        sys.executable,
        str(BUAA_REPLAY_SCRIPT),
        course_page_url,
        "--output-dir",
        replay_output_dir,
        "--extract-existing",
        "--preferred-stream",
        config.get("preferred_replay_stream", "teacher"),
    ]
    if sub_ids:
        cmd.extend(["--only-sub-ids", ",".join(sub_ids)])
    if dates:
        cmd.extend(["--only-dates", ",".join(dates)])
    if config.get("student"):
        cmd.extend(["--student", str(config["student"])])
    if config.get("lightweight_teacher_review"):
        cmd.append("--lightweight-teacher-review")
        cmd.extend(["--teacher-review-max-windows", str(config.get("teacher_review_max_windows", 3))])
    if config.get("browser_runtime_auth"):
        cmd.append("--browser-runtime-auth")
    if config.get("browser_runtime_profile_dir"):
        cmd.extend(["--browser-runtime-profile-dir", str(config["browser_runtime_profile_dir"])])
    if config.get("browser_login_timeout"):
        cmd.extend(["--browser-login-timeout", str(config["browser_login_timeout"])])
    if config.get("browser_channel"):
        cmd.extend(["--browser-channel", str(config["browser_channel"])])
    completed = subprocess.run(
        cmd,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=utf8_env(),
    )
    return {
        "status": "ok" if completed.returncode == 0 else "error",
        "returncode": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
    }


def build_replay_draft_note(
    config: dict[str, Any],
    course_name: str,
    course_dir: Path,
    lesson_item: dict[str, Any],
    lesson_dir: Path,
) -> dict[str, Any]:
    metadata_path = lesson_dir / "metadata.json"
    if not metadata_path.exists():
        return {"status": "skipped", "reason": "missing_metadata", "sub_id": lesson_item["sub_id"]}
    metadata = read_json(metadata_path)
    outline_dir = lesson_dir / "ppt_outline"
    outline_slides = load_outline_slides(outline_dir)
    transcript_segments = load_transcript_segments(lesson_dir)
    diagnosis = build_replay_diagnosis(metadata, transcript_segments, outline_slides)
    date = lesson_item.get("date") or lesson_date_from_name(str(metadata.get("start_at", "")))
    sub_title = normalize_sub_title(lesson_item.get("sub_title") or metadata.get("sub_title", lesson_item["sub_id"]))
    lesson_title = f"{date} {sub_title}".strip()
    lesson_path = course_dir / "课次" / f"{lesson_title}.md"
    if lesson_path.exists():
        existing_frontmatter, _ = extract_frontmatter_and_body(read_text(lesson_path))
        existing_source = str(existing_frontmatter.get("source") or "")
        if existing_source not in GENERATED_REPLAY_SOURCES:
            return {
                "status": "skipped",
                "reason": "existing_non_generated_note",
                "sub_id": lesson_item["sub_id"],
                "path": str(lesson_path),
            }
        if existing_source == "buaa-replay-semantic-rebuild" or bool(existing_frontmatter.get("semantic_rebuild_completed")):
            return {
                "status": "skipped",
                "reason": "existing_semantic_note",
                "sub_id": lesson_item["sub_id"],
                "path": str(lesson_path),
            }
        lesson_path.unlink()

    transcript_path = lesson_dir / "transcript.txt"
    assignment_hits = collect_transcript_hits(transcript_path, ["作业", "习题", "练习"])
    exam_hits = collect_transcript_hits(transcript_path, ["考试", "期中", "期末"])
    notice_hits = collect_transcript_hits(transcript_path, ["通知", "下周", "提交", "截止"])
    transcript_sections = build_transcript_fallback_sections(transcript_segments, transcript_path)
    mainline = build_final_mainline(transcript_sections)
    if not mainline:
        mainline = ["本节主要内容仍需结合课程转写与教师流进一步整理。"]

    lines = [
        f"# {lesson_title}",
        "",
        "## 元信息",
        "",
        f"- 课程：[[01-Courses/{course_name}/00-课程总览]]",
        f"- 日期：{date}",
        f"- 节次：{sub_title}",
        f"- 来源：BUAA 课程回放草稿（课程转写主线）",
        f"- 回放子课 ID：{lesson_item['sub_id']}",
        f"- 默认回放流：{metadata.get('preferred_stream', config.get('preferred_replay_stream', 'teacher'))}",
        f"- 回放页：{lesson_item.get('livingroom_url', '')}",
        "",
        "## 本节主线",
        "",
    ]
    lines.extend(f"- {item}" for item in mainline)
    lines.extend(["", "## 内容纪要", ""])
    if transcript_sections:
        for section in transcript_sections:
            start_text = format_seconds(section.get("start_sec", 0))
            end_text = format_seconds(section.get("end_sec", 0))
            lines.append(f"### {display_section_title(section)}")
            lines.append("")
            if start_text and end_text:
                lines.append(f"时间参考：约 `{start_text}-{end_text}`")
            elif start_text:
                lines.append(f"时间参考：约 `{start_text}`")
            lines.append("")
            transcript_lines = transcript_lines_in_range(
                transcript_segments,
                float(section.get("start_sec", 0)),
                float(section.get("end_sec", 0)),
            )
            bullets = render_final_section_bullets(section, transcript_lines)
            if bullets:
                lines.extend(f"- {bullet}" for bullet in bullets)
            else:
                lines.append("- 当前 transcript 可以支持粗粒度摘要，但这段的稳定要点仍需语义重建进一步压实。")
            lines.append("")
    else:
        lines.extend(
            [
                "- 当前课程转写还不足以稳定分出本节结构，建议先做语义重建。",
                "",
            ]
        )
    lines.extend(["## 课程事务", "", "### 作业", ""])
    if assignment_hits:
        lines.extend(f"- {item}" for item in assignment_hits)
    else:
        lines.append("- 当前未从转写中识别出稳定的作业信息。")
    lines.extend(["", "### 考试", ""])
    if exam_hits:
        lines.extend(f"- {item}" for item in exam_hits)
    else:
        lines.append("- 当前未从转写中识别出稳定的考试安排。")
    lines.extend(["", "### 通知", ""])
    if notice_hits:
        lines.extend(f"- {item}" for item in notice_hits)
    else:
        lines.append("- 当前未从转写中识别出稳定的课程通知。")
    lines.extend(
        [
            "",
            "## 参考材料",
            "",
            f"- 回放抽取目录：`{lesson_dir}`",
        f"- 课程转写：`{transcript_path}`",
        ]
    )
    outline_md_path = outline_dir / "ppt_outline.md"
    lines.extend(
        [
            "",
            "## 本节提到的概念",
            "",
            "- 待补充",
            "",
            "## 待核对",
            "",
        ]
    )
    lines.extend(
        [
            "- 如果本节包含课堂展示或学生汇报，需要结合教师流区分“学生展示 / 老师点评 / 正式课程内容”。",
            "- OCR 可能误识别专有名词、公式和页眉页脚，回看时优先核对关键术语。",
        ]
    )

    body = "\n".join(lines)
    write_text(lesson_path, body)
    upsert_frontmatter(
        lesson_path,
        {
            "type": "lesson",
            "course": course_name,
            "title": lesson_title,
            "date": date,
            "source": "buaa-replay-draft",
            "replay_sub_id": lesson_item["sub_id"],
            "preferred_stream": metadata.get("preferred_stream", config.get("preferred_replay_stream", "teacher")),
            "has_ppt_outline": bool(outline_slides),
            "draft_basis": diagnosis["draft_basis"],
            "replay_diagnosis": diagnosis["status"],
        },
    )
    return {
        "status": "created",
        "sub_id": lesson_item["sub_id"],
        "path": str(lesson_path),
        "has_ppt_outline": bool(outline_slides),
        "outline_group_count": 0,
    }


def build_partial_transcript_note(
    config: dict[str, Any],
    course_name: str,
    course_dir: Path,
    lesson_item: dict[str, Any],
    lesson_dir: Path,
    metadata: dict[str, Any],
    transcript_segments: list[dict[str, Any]],
    coverage: dict[str, Any],
) -> dict[str, Any]:
    outline_dir = lesson_dir / "ppt_outline"
    outline_slides = load_outline_slides(outline_dir)
    existing_concepts = load_existing_course_concepts(course_dir.parent.parent, course_name)
    existing_concept_set = set(existing_concepts)
    date = lesson_item.get("date") or lesson_date_from_name(str(metadata.get("start_at", "")))
    sub_title = normalize_sub_title(lesson_item.get("sub_title") or metadata.get("sub_title", lesson_item["sub_id"]))
    lesson_title = f"{date} {sub_title}".strip()
    lesson_path = course_dir / "课次" / f"{lesson_title}.md"
    start_text = format_seconds(coverage.get("last_transcript_sec", 0))
    duration_text = format_seconds(coverage.get("duration_sec", 0))
    transcript_path = lesson_dir / "transcript.txt"
    lines = [
        f"# {lesson_title}",
        "",
        "## 元信息",
        "",
        f"- 课程：[[01-Courses/{course_name}/00-课程总览]]",
        f"- 日期：{date}",
        f"- 节次：{sub_title}",
        "- 来源：BUAA 回放诊断性草稿",
        f"- 回放子课 ID：{lesson_item['sub_id']}",
        f"- 默认回放流：{metadata.get('preferred_stream', config.get('preferred_replay_stream', 'teacher'))}",
        f"- 回放页：{lesson_item.get('livingroom_url', '')}",
        "",
        "## 当前判断",
        "",
        f"- 当前 transcript 只覆盖到约 `{start_text}`，而整节课时长约 `{duration_text}`，覆盖率约 `{coverage.get('coverage_ratio', 0):.0%}`。",
        "- 在这种原料条件下，不能直接把 `final` / `final-explained` 的自动重建结果当成正式课次纪要。",
    ]
    lines.extend(
        [
            "",
            "## 当前流程暴露的问题",
            "",
            "- 原料并非完全缺失，而是 transcript 严重截断，导致 transcript 和 PPT 覆盖范围不一致。",
            "- 如果继续按正式模式重建，生成器会要么退化成前一节模板，要么过拟合 PPT 页标题，把许多页直接切成低质量小节。",
            "",
            "## 下一步建议",
            "",
            "- 先不要把这节课记为正式完成稿。",
            "- 等平台补齐 transcript 后，再重新运行 `final` / `final-explained`。",
            "- 如果必须现在推进，应改成人工语义重建，而不是直接接受自动结果。",
            "",
            "## 参考材料",
            "",
            f"- 回放抽取目录：`{lesson_dir}`",
            f"- Transcript：`{transcript_path}`",
        ]
    )
    lines.extend(
        [
            "",
            "## 待核对",
            "",
            "- transcript 是否只是平台阶段性转写，后续还会继续补齐。",
            "- 当前 PPT 中后半段的技术内容在这节里究竟展开到了哪一层。",
            "- 若继续推进，这节应优先做人工重写，而不是直接接受自动生成结果。",
        ]
    )
    body = "\n".join(lines)
    write_text(lesson_path, body)
    review_items = [
        f"当前 transcript 只覆盖到约 {start_text}，而整节课时长约 {duration_text}，覆盖率明显不足。",
        "在 transcript 与 PPT 覆盖范围严重不一致的情况下，自动重建结果不能直接视为正式纪要。",
    ]
    upsert_frontmatter(
        lesson_path,
        {
            "type": "lesson",
            "course": course_name,
            "title": lesson_title,
            "date": date,
            "source": "buaa-replay-partial-transcript",
            "replay_sub_id": lesson_item["sub_id"],
            "preferred_stream": metadata.get("preferred_stream", config.get("preferred_replay_stream", "teacher")),
            "has_ppt_outline": bool(outline_slides),
            "has_teacher_review": False,
            "draft_basis": "partial_transcript_plus_ppt" if outline_slides else "partial_transcript_only",
            "replay_diagnosis": "partial_transcript",
            "rebuild_mode": "partial-transcript-diagnostic",
            "has_semantic_rebuild_packet": False,
            "concepts": [],
            "review_items": review_items,
            "transcript_coverage_ratio": coverage.get("coverage_ratio", 0),
            "transcript_last_sec": coverage.get("last_transcript_sec", 0),
            "transcript_duration_sec": coverage.get("duration_sec", 0),
        },
    )
    return {
        "status": "created",
        "sub_id": lesson_item["sub_id"],
        "path": str(lesson_path),
        "has_ppt_outline": bool(outline_slides),
        "has_teacher_review": False,
        "has_semantic_rebuild_packet": False,
        "section_count": 0,
        "diagnostic": "partial_transcript",
        "transcript_coverage_ratio": coverage.get("coverage_ratio", 0),
    }


def build_waiting_transcript_note(
    config: dict[str, Any],
    course_name: str,
    course_dir: Path,
    lesson_item: dict[str, Any],
    lesson_dir: Path,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    outline_dir = lesson_dir / "ppt_outline"
    outline_slides = load_outline_slides(outline_dir)
    date = lesson_item.get("date") or lesson_date_from_name(str(metadata.get("start_at", "")))
    sub_title = normalize_sub_title(lesson_item.get("sub_title") or metadata.get("sub_title", lesson_item["sub_id"]))
    lesson_title = f"{date} {sub_title}".strip()
    lesson_path = course_dir / "课次" / f"{lesson_title}.md"
    lines = [
        f"# {lesson_title}",
        "",
        "## 元信息",
        "",
        f"- 课程：[[01-Courses/{course_name}/00-课程总览]]",
        f"- 日期：{date}",
        f"- 节次：{sub_title}",
        "- 来源：BUAA 待转写占位页",
        f"- 回放子课 ID：{lesson_item['sub_id']}",
        f"- 默认回放流：{metadata.get('preferred_stream', config.get('preferred_replay_stream', 'teacher'))}",
        f"- 回放页：{lesson_item.get('livingroom_url', '')}",
        "",
        "## 当前判断",
        "",
        "- 平台当前还没有提供可用 transcript，因此不能正式重建课次纪要。",
    ]
    lines.extend(
        [
            "",
            "## 下一步建议",
            "",
            "- 先等待平台补齐 transcript，再重跑正式重建。",
            "- 如果这节课必须优先推进，应改成人工语义重写，而不是直接接受脚本 seed。",
            "",
            "## 参考材料",
            "",
            f"- 回放抽取目录：`{lesson_dir}`",
            f"- Transcript：`{lesson_dir / 'transcript.txt'}`",
        ]
    )
    write_text(lesson_path, "\n".join(lines))
    upsert_frontmatter(
        lesson_path,
        {
            "type": "lesson",
            "course": course_name,
            "title": lesson_title,
            "date": date,
            "source": "buaa-replay-waiting-transcript",
            "replay_sub_id": lesson_item["sub_id"],
            "preferred_stream": metadata.get("preferred_stream", config.get("preferred_replay_stream", "teacher")),
            "has_transcript": False,
            "has_ppt_outline": bool(outline_slides),
            "draft_basis": "ppt_outline" if outline_slides else "waiting_transcript",
            "rebuild_mode": "pending-transcript",
            "has_semantic_rebuild_packet": False,
            "concepts": [],
            "review_items": ["平台当前还没有提供可用 transcript，暂时不能正式重建课次纪要。"],
        },
    )
    return {
        "status": "created",
        "sub_id": lesson_item["sub_id"],
        "path": str(lesson_path),
        "has_ppt_outline": bool(outline_slides),
        "has_teacher_review": False,
        "has_semantic_rebuild_packet": False,
        "diagnostic": "waiting_transcript",
    }


def build_needs_review_note(
    course_name: str,
    course_dir: Path,
    lesson_item: dict[str, Any],
    lesson_dir: Path,
    metadata: dict[str, Any],
    summary_coverage: dict[str, Any],
    outline_slides: list[dict[str, Any]],
) -> dict[str, Any]:
    date = lesson_item.get("date") or lesson_date_from_name(str(metadata.get("start_at", "")))
    sub_title = normalize_sub_title(lesson_item.get("sub_title") or metadata.get("sub_title", lesson_item["sub_id"]))
    lesson_title = f"{date} {sub_title}".strip()
    lesson_path = course_dir / "课次" / f"{lesson_title}.md"
    covered_until_text = format_seconds(summary_coverage.get("covered_until_sec", 0))
    lines = [
        f"# {lesson_title}",
        "",
        "## 当前判断",
        "",
        f"- transcript 已可用，但当前自动摘要只稳定覆盖到约 `{covered_until_text}`。",
        f"- 摘要覆盖率约 `{summary_coverage.get('coverage_ratio', 0):.0%}`，因此这节课目前只能记为 `needs review`，不能当正式课次页。",
        "- 这通常说明 transcript 主线尚未被完整提炼，应继续做语义重建，而不是直接落正式笔记。",
        "",
        "## 参考材料",
        "",
        f"- 回放抽取目录：`{lesson_dir}`",
        f"- Transcript：`{lesson_dir / 'transcript.txt'}`",
    ]
    if outline_slides:
        lines.append("- PPT 提纲存在，但只能作为术语、标题、公式符号和事务截图的辅助参考。")
    write_text(lesson_path, "\n".join(lines))
    upsert_frontmatter(
        lesson_path,
        {
            "type": "lesson",
            "course": course_name,
            "title": lesson_title,
            "date": date,
            "source": "buaa-replay-needs-review",
            "replay_sub_id": lesson_item["sub_id"],
            "preferred_stream": metadata.get("preferred_stream", "teacher"),
            "has_transcript": True,
            "has_ppt_outline": bool(outline_slides),
            "draft_basis": "transcript_primary",
            "replay_diagnosis": "needs_review",
            "rebuild_mode": "needs-review",
            "has_semantic_rebuild_packet": False,
            "concepts": [],
            "review_items": [
                "transcript 已可用，但当前摘要覆盖不完整，需继续语义重建后才能视为正式课次页。"
            ],
        },
    )
    return {
        "status": "created",
        "sub_id": lesson_item["sub_id"],
        "path": str(lesson_path),
        "has_ppt_outline": bool(outline_slides),
        "has_teacher_review": False,
        "has_semantic_rebuild_packet": False,
        "diagnostic": "needs_review",
    }


def build_replay_final_note(
    config: dict[str, Any],
    course_name: str,
    course_dir: Path,
    lesson_item: dict[str, Any],
    lesson_dir: Path,
    mode: str = "final",
) -> dict[str, Any]:
    metadata_path = lesson_dir / "metadata.json"
    if not metadata_path.exists():
        return {"status": "skipped", "reason": "missing_metadata", "sub_id": lesson_item["sub_id"]}
    metadata = read_json(metadata_path)
    outline_dir = lesson_dir / "ppt_outline"
    outline_slides = load_outline_slides(outline_dir)
    transcript_segments = load_transcript_segments(lesson_dir)
    diagnosis = build_replay_diagnosis(metadata, transcript_segments, outline_slides)
    transcript_path = lesson_dir / "transcript.txt"
    if diagnosis["status"] == "waiting_transcript":
        return build_waiting_transcript_note(
            config,
            course_name,
            course_dir,
            lesson_item,
            lesson_dir,
            metadata,
        )
    if diagnosis["status"] == "partial_transcript":
        return build_partial_transcript_note(
            config,
            course_name,
            course_dir,
            lesson_item,
            lesson_dir,
            metadata,
            transcript_segments,
            diagnosis["coverage"],
        )
    sections = build_transcript_fallback_sections(transcript_segments, transcript_path)
    affairs = build_replay_affairs_summary(transcript_path)
    teacher_review = load_teacher_review_payload(lesson_dir)
    teacher_review_windows = teacher_review.get("windows", []) if isinstance(teacher_review, dict) else []
    teacher_review_confirmed = teacher_review.get("confirmed_items", []) if isinstance(teacher_review, dict) else []
    teacher_review_questions = teacher_review.get("review_questions", []) if isinstance(teacher_review, dict) else []
    review_items = build_final_review_items(transcript_path, sections)
    date = lesson_item.get("date") or lesson_date_from_name(str(metadata.get("start_at", "")))
    sub_title = normalize_sub_title(lesson_item.get("sub_title") or metadata.get("sub_title", lesson_item["sub_id"]))
    lesson_title = f"{date} {sub_title}".strip()
    lesson_path = course_dir / "课次" / f"{lesson_title}.md"
    if lesson_path.exists():
        existing_frontmatter, _ = extract_frontmatter_and_body(read_text(lesson_path))
        if existing_frontmatter.get("source") not in GENERATED_REPLAY_SOURCES:
            return {
                "status": "skipped",
                "reason": "existing_non_generated_note",
                "sub_id": lesson_item["sub_id"],
                "path": str(lesson_path),
            }

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
    summary_coverage = summary_coverage_info(transcript_segments, sections)
    if summary_coverage["insufficient"]:
        return build_needs_review_note(
            course_name,
            course_dir,
            lesson_item,
            lesson_dir,
            metadata,
            summary_coverage,
            outline_slides,
        )
    hubs, existing_concepts, _graph_bootstrap = ensure_semantic_graph_bootstrap(
        course_name,
        course_dir,
        config,
        lesson_title,
        [],
        mode,
    )
    existing_concept_set = set(existing_concepts)
    mainline_items = build_final_mainline(sections)
    section_payloads: list[dict[str, Any]] = []
    semantic_artifacts: dict[str, str] = {}
    transcript_overview = transcript_overview_payload(transcript_segments)
    packet_mode = "final-explained" if mode == "final" else mode
    semantic_mode = packet_mode in SEMANTIC_REBUILD_MODES

    lines = [
        f"# {lesson_title}",
        "",
        "## 元信息",
        "",
        f"- 课程：[[01-Courses/{course_name}/00-课程总览]]",
        f"- 日期：{date}",
        f"- 节次：{sub_title}",
        "- 来源：BUAA 课程回放重建纪要",
        f"- 回放子课 ID：{lesson_item['sub_id']}",
        f"- 默认回放流：{metadata.get('preferred_stream', config.get('preferred_replay_stream', 'teacher'))}",
        f"- 回放页：{lesson_item.get('livingroom_url', '')}",
        "",
        "## 本节主线",
        "",
    ]
    lines.extend(f"- {item}" for item in mainline_items)
    lines.extend(["", "## 内容纪要", ""])
    if section_contexts:
        for section, transcript_lines in section_contexts:
            lines.append(f"### {section['display_title']}")
            lines.append("")
            start_text = format_seconds(section.get("start_sec", 0))
            end_text = format_seconds(section.get("end_sec", 0))
            if start_text and end_text:
                lines.append(f"时间参考：约 `{start_text}-{end_text}`")
                lines.append("")
            for bullet in render_final_section_bullets(section, transcript_lines):
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
                    "evidence_policy": "Use this only as a time window. Read transcript.txt before writing; do not quote raw ASR snippets.",
                }
            )
    else:
        lines.extend(["- 当前材料还不足以稳定重建成最终纪要。", ""])

    lines.extend(["## 课程事务", "", "### 作业", ""])
    lines.extend(f"- {item}" for item in affairs["assignment"])
    lines.extend(["", "### 考试", ""])
    lines.extend(f"- {item}" for item in affairs["exam"])
    lines.extend(["", "### 课程安排", ""])
    lines.extend(f"- {item}" for item in affairs["arrangement"])
    lines.extend(
        [
            "",
            "### 阅读建议",
            "",
            "- 回看这一节时，可以先抓住三件事：这节课想解决什么问题、方法主线是什么、例子或应用场景为什么会被反复强调。",
            "- 如果后续要整理概念页，建议优先沉淀本节最稳定的几个主题，再回头补术语和公式细节。",
            "",
            "### 通知",
            "",
        ]
    )
    lines.extend(f"- {item}" for item in affairs["notice"])
    if teacher_review_windows:
        lines.extend(
            [
                "",
                "### 事务复核",
                "",
                "- 当前只准备了教师流事务片段，尚未自动改写为“已复核结论”。",
                "- 只有后续人工或二次流程确认后，才应把相关条目标成“已通过教师流复核”。",
                "",
            ]
        )
        if teacher_review_confirmed:
            lines.append("#### 已通过教师流复核")
            lines.append("")
            lines.extend(f"- {item}" for item in teacher_review_confirmed)
            lines.append("")
        if teacher_review_questions:
            lines.append("#### 推荐优先确认")
            lines.append("")
            lines.extend(f"- {item}" for item in teacher_review_questions[:3])
            lines.append("")
        for window in teacher_review_windows:
            lines.append(f"- 时间：`{window.get('start_hms', '')}-{window.get('end_hms', '')}`")
            if window.get("excerpts"):
                lines.append(f"  摘录：{'；'.join(window.get('excerpts', [])[:3])}")
            lines.append(f"  教师流片段：`{window.get('clip_path', '')}`")
    lines.extend(
        [
            "",
            "## 参考材料",
            "",
            f"- 回放抽取目录：`{lesson_dir}`",
            f"- Transcript：`{transcript_path}`",
        ]
    )
    if semantic_mode:
        course_alignment = build_course_alignment_context(
            course_name,
            course_dir,
            hubs,
            lesson_title,
            [],
        )
        semantic_packet = {
            "mode": packet_mode,
            "course_name": course_name,
            "lesson_title": lesson_title,
            "lesson_note_path": str(lesson_path),
            "lesson_dir": str(lesson_dir),
            "metadata": {
                "date": date,
                "sub_title": sub_title,
                "replay_sub_id": lesson_item["sub_id"],
                "preferred_stream": metadata.get("preferred_stream", config.get("preferred_replay_stream", "teacher")),
                "livingroom_url": lesson_item.get("livingroom_url", ""),
                "has_ppt_outline": bool(outline_slides),
                "has_ppt_artifact": diagnosis["has_ppt_artifact"],
                "has_teacher_review": bool(teacher_review_windows),
                "source_profile": diagnosis["source_profile"],
                "section_strategy": diagnosis["section_strategy"],
            },
            "constraints": {
                "transcript_first": True,
                "ppt_assisted": True,
                "keep_uncertainty_explicit": True,
                "concept_links_required": True,
                "allow_short_concept_explanations": mode == "final-explained",
            },
            "transcript_overview": transcript_overview,
            "sections": section_payloads,
            "affairs": affairs,
            "review_items": review_items,
            "teacher_review": {
                "confirmed_items": teacher_review_confirmed,
                "review_questions": teacher_review_questions,
                "windows": teacher_review_windows,
            },
            "course_context": {
                "hub_summaries": summarize_hubs(hubs),
                "graph_growth": load_graph_growth_context(course_name, course_dir, hubs),
                "recent_lessons": course_alignment.get("recent_lessons", []),
            },
            "course_alignment": course_alignment,
            "replay_diagnosis": diagnosis,
            "references": {
                "transcript": str(transcript_path),
                "ppt_outline": str(outline_dir / "ppt_outline.md"),
                "teacher_review": str(lesson_dir / "teacher_review" / "teacher_review.json"),
                "course_overview": str(course_dir / "00-课程总览.md"),
            },
        }
        semantic_artifacts = write_semantic_rebuild_artifacts(lesson_dir, semantic_packet, packet_mode)
        return {
            "status": "pending_semantic",
            "sub_id": lesson_item["sub_id"],
            "path": str(lesson_path),
            "has_ppt_outline": bool(outline_slides),
            "has_teacher_review": bool(teacher_review_windows),
            "has_semantic_rebuild_packet": True,
            "semantic_rebuild_input": semantic_artifacts.get("input_path", ""),
            "quality_gate": "final_note_must_be_written_by_agent_and_pass_validate_final_note",
            "section_count": len(sections),
        }
        lines.append(f"- 语义重建输入：`{semantic_artifacts['input_path']}`")
        lines.append(f"- 语义重建提示：`{semantic_artifacts['prompt_path']}`")
    if teacher_review_windows:
        lines.append(f"- 教师流事务复核：`{lesson_dir / 'teacher_review' / 'teacher_review.json'}`")
    lines.extend(["", "## 待核对", ""])
    lines.extend(f"- {item}" for item in review_items)

    body = "\n".join(lines)
    quality_issues = validate_markdown_text(body)
    if quality_issues:
        if lesson_path.exists():
            lesson_path.unlink()
        return {
            "status": "blocked_by_quality_gate",
            "sub_id": lesson_item["sub_id"],
            "path": str(lesson_path),
            "quality_issues": quality_issues,
        }
    write_text(lesson_path, body)
    upsert_frontmatter(
        lesson_path,
        {
            "type": "lesson",
            "course": course_name,
            "title": lesson_title,
            "date": date,
            "source": "buaa-replay-rebuild",
            "replay_sub_id": lesson_item["sub_id"],
            "preferred_stream": metadata.get("preferred_stream", config.get("preferred_replay_stream", "teacher")),
            "has_ppt_outline": bool(outline_slides),
            "has_teacher_review": bool(teacher_review_windows),
            "draft_basis": diagnosis["draft_basis"],
            "replay_diagnosis": diagnosis["status"],
            "rebuild_mode": mode,
            "has_semantic_rebuild_packet": bool(semantic_artifacts),
            "semantic_rebuild_status": "required" if semantic_artifacts else "",
            "concepts": [],
            "review_items": review_items,
        },
    )
    return {
        "status": "created",
        "sub_id": lesson_item["sub_id"],
        "path": str(lesson_path),
        "has_ppt_outline": bool(outline_slides),
        "has_teacher_review": bool(teacher_review_windows),
        "has_semantic_rebuild_packet": bool(semantic_artifacts),
        "semantic_rebuild_input": semantic_artifacts.get("input_path", ""),
        "section_count": len(sections),
    }


def build_replay_note(
    config: dict[str, Any],
    course_name: str,
    course_dir: Path,
    lesson_item: dict[str, Any],
    lesson_dir: Path,
    mode: str,
) -> dict[str, Any]:
    if mode == "draft":
        return build_replay_draft_note(config, course_name, course_dir, lesson_item, lesson_dir)
    return build_replay_final_note(config, course_name, course_dir, lesson_item, lesson_dir, mode=mode)


def draft_replay_lessons(
    config: dict[str, Any],
    course_name: str,
    course_dir: Path,
    sub_ids: list[str],
    dates: list[str],
    mode: str,
) -> dict[str, Any]:
    replay_output_dir = config.get("replay_output_dir", "")
    if not replay_output_dir:
        return {"status": "skipped", "reason": "missing_replay_output_dir"}
    extract_result = ensure_replay_extracts(
        config,
        sub_ids,
        dates,
        require_ppt_outline=False,
    )
    if extract_result.get("status") == "error":
        return {"status": "error", "reason": extract_result.get("stderr") or extract_result.get("stdout") or "extract_failed"}

    output_dir = Path(replay_output_dir)
    lesson_index_path = output_dir / "lesson_index.json"
    if not lesson_index_path.exists():
        return {"status": "error", "reason": "missing_lesson_index"}
    lesson_index = read_json(lesson_index_path)
    selected = []
    for item in lesson_index:
        if sub_ids and item.get("sub_id") in sub_ids:
            selected.append(item)
            continue
        if dates and item.get("date") in dates:
            selected.append(item)
    selected = [item for item in selected if item.get("replay_ready")]
    results: list[dict[str, Any]] = []
    for item in selected:
        lesson_dir = output_dir / "lessons" / str(item["sub_id"])
        results.append(build_replay_note(config, course_name, course_dir, item, lesson_dir, mode))
    for item in results:
        if item.get("status") != "pending_semantic":
            continue
        lesson_path = Path(str(item.get("path") or ""))
        if not lesson_path.exists():
            continue
        frontmatter, _ = extract_frontmatter_and_body(read_text(lesson_path))
        if str(frontmatter.get("source") or "") in GENERATED_REPLAY_SOURCES and not bool(frontmatter.get("semantic_rebuild_completed")):
            lesson_path.unlink()
    created = [item for item in results if item.get("status") == "created"]
    pending_semantic = [item for item in results if item.get("status") == "pending_semantic"]
    return {
        "status": "ok",
        "mode": mode,
        "selected_count": len(selected),
        "created_count": len(created),
        "pending_semantic_count": len(pending_semantic),
        "results": results,
        "extract": extract_result,
    }


def sync_buaa_replays(config: dict[str, Any], course_dir: Path, lesson_summaries: list[dict[str, Any]]) -> dict[str, Any]:
    course_page_url = config.get("course_page_url", "")
    replay_output_dir = config.get("replay_output_dir", "")
    if not course_page_url or not replay_output_dir:
        return {"status": "skipped", "reason": "missing_course_page_url_or_replay_output_dir"}
    output_dir = Path(replay_output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        str(BUAA_REPLAY_SCRIPT),
        course_page_url,
        "--output-dir",
        str(output_dir),
        "--check-new-replays",
    ]
    student = config.get("student", "")
    preferred_replay_stream = config.get("preferred_replay_stream", "teacher")
    if student:
        cmd.extend(["--student", student])
    if config.get("browser_runtime_auth"):
        cmd.append("--browser-runtime-auth")
    if config.get("browser_runtime_profile_dir"):
        cmd.extend(["--browser-runtime-profile-dir", str(config["browser_runtime_profile_dir"])])
    if config.get("browser_login_timeout"):
        cmd.extend(["--browser-login-timeout", str(config["browser_login_timeout"])])
    if config.get("browser_channel"):
        cmd.extend(["--browser-channel", str(config["browser_channel"])])
    sync_error = ""
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, encoding="utf-8", env=utf8_env())
    except subprocess.CalledProcessError as exc:
        sync_error = (exc.stderr or exc.stdout or str(exc)).strip()

    lesson_index_path = output_dir / "lesson_index.json"
    if not lesson_index_path.exists():
        write_text(
            course_dir / "回放同步.md",
            "\n".join(
                [
                    "# 回放同步",
                    "",
                    f"- 课程页：{course_page_url}",
                    "- 当前同步失败，且本地没有可复用的回放索引。",
                    f"- 失败原因：{sync_error or 'unknown_error'}",
                ]
            ),
        )
        write_text(course_dir / "待整理回放.md", "# 待整理回放\n\n- 当前无法生成待整理清单，因为回放同步失败。\n")
        return {"status": "error", "reason": sync_error or "sync_failed_without_cache"}

    lesson_index = json.loads(read_text(lesson_index_path))
    replay_check_path = output_dir / "new_replay_check.json"
    replay_check = json.loads(read_text(replay_check_path)) if replay_check_path.exists() else {}
    summarized_dates = {lesson["date"] for lesson in lesson_summaries if lesson["date"]}
    pending_semantic = semantic_rebuild_pending_lessons(course_dir, output_dir)
    pending_semantic_sub_ids = {item["sub_id"] for item in pending_semantic if item.get("sub_id")}
    ignored_dates = set(config.get("ignored_replay_dates", []))
    ignored_sub_ids = set(config.get("ignored_replay_sub_ids", []))

    backlog: list[dict[str, Any]] = []
    upcoming: list[dict[str, Any]] = []
    review_candidates: list[dict[str, Any]] = []
    for item in lesson_index:
        if item["sub_id"] in ignored_sub_ids or item["date"] in ignored_dates:
            continue
        if item["replay_ready"]:
            if item["sub_id"] in pending_semantic_sub_ids:
                continue
            if item["date"] not in summarized_dates:
                backlog.append(item)
            else:
                reasons = source_upgrade_reasons(course_dir, output_dir, item)
                if reasons:
                    review_candidates.append({**item, "review_reasons": reasons})
        else:
            upcoming.append(item)

    sync_lines = [
        "# 回放同步",
        "",
        f"- 课程页：{course_page_url}",
        f"- 默认回放流：{preferred_replay_stream}",
        f"- 最近检查：{replay_check.get('checked_at', '')}",
        f"- 已有回放：{sum(1 for item in lesson_index if item['replay_ready'])}",
        f"- 新增回放：{replay_check.get('new_replay_count', 0)}",
    ]
    if sync_error:
        sync_lines.append(f"- 本次在线同步失败，以下内容来自已有缓存：{sync_error}")
    sync_lines.extend(["", "## 新增回放", ""])
    new_replays = replay_check.get("new_replays", [])
    if new_replays:
        for item in new_replays:
            sync_lines.append(f"- {item.get('date', '')} {item.get('sub_title', '')} ({item.get('sub_id', '')})")
    else:
        sync_lines.append("- 当前没有检测到新增回放。")
    sync_lines.extend(["", "## 待整理回放", ""])
    if backlog:
        for item in backlog:
            sync_lines.append(f"- {item['date']} {item['sub_title']} ({item['sub_id']})")
    else:
        sync_lines.append("- 当前没有待整理回放。")
    sync_lines.extend(["", "## 待语义重建课次", ""])
    if pending_semantic:
        for item in pending_semantic:
            suffix = f" ({item['sub_id']})" if item["sub_id"] else ""
            mode_text = f" | 模式：{item['mode']}" if item["mode"] else ""
            sync_lines.append(f"- {item['date']} {item['title'].replace(item['date'], '', 1).strip()}{suffix}{mode_text}")
    else:
        sync_lines.append("- 当前没有待语义重建的课次。")
    sync_lines.extend(["", "## 建议复查已整理课次", ""])
    if review_candidates:
        for item in review_candidates:
            sync_lines.append(f"- {item['date']} {item['sub_title']} ({item['sub_id']})")
            for reason in item["review_reasons"]:
                sync_lines.append(f"  - {reason}")
    else:
        sync_lines.append("- 当前没有因原料升级而建议复查的已整理课次。")
    sync_lines.extend(["", "## 尚未回放", ""])
    if upcoming:
        for item in upcoming:
            sync_lines.append(f"- {item['date']} {item['sub_title']} ({item['sub_id']})")
    else:
        sync_lines.append("- 课程页当前没有尚未回放的课次。")
    write_text(course_dir / "回放同步.md", "\n".join(sync_lines))

    backlog_lines = [
        "# 待整理回放",
        "",
        f"- 最近检查：{replay_check.get('checked_at', '')}",
        f"- 默认回放流：{preferred_replay_stream}",
        "",
    ]
    if backlog:
        for item in backlog:
            backlog_lines.append(f"- [ ] {item['date']} {item['sub_title']} | 回放：{item['livingroom_url']}")
    else:
        backlog_lines.append("- 当前没有待整理回放。")
    write_text(course_dir / "待整理回放.md", "\n".join(backlog_lines))

    return {
        "status": "ok_cached" if sync_error else "ok",
        "replay_ready_lessons": sum(1 for item in lesson_index if item["replay_ready"]),
        "new_replay_count": replay_check.get("new_replay_count", 0),
        "backlog_count": len(backlog),
        "pending_semantic_count": len(pending_semantic),
        "review_candidate_count": len(review_candidates),
        "upcoming_count": len(upcoming),
        "warning": sync_error,
        "review_candidates": review_candidates,
    }


def scan_noise(vault_dir: Path) -> dict[str, Any]:
    findings: list[dict[str, str]] = []
    for path in vault_dir.rglob("*"):
        if path.is_dir():
            continue
        if any(part.startswith(".obsidian") for part in path.parts):
            continue
        if "04-Templates" in path.parts:
            continue
        rel_path = path.relative_to(vault_dir)
        name = path.name
        reasons: list[str] = []
        if re.match(r"^未命名(\s+\d+)?\.(canvas|md|base)$", name):
            reasons.append("unnamed_draft")
        if name in {"概念名.md", "课次名.md"}:
            reasons.append("placeholder_file_name")
        if path.suffix.lower() in {".md", ".canvas", ".base"}:
            text = read_text(path)
            if not text.strip():
                reasons.append("empty_file")
            elif any(token in text for token in PLACEHOLDER_TOKENS):
                reasons.append("placeholder_content")
        if reasons:
            findings.append({"path": str(rel_path).replace("\\", "/"), "reasons": ",".join(reasons)})
    report_lines = [
        "# 图谱噪声治理",
        "",
        f"- 当前识别到可疑噪声文件：{len(findings)}",
        "",
        "| 路径 | 原因 | 建议 |",
        "| --- | --- | --- |",
    ]
    for item in findings:
        report_lines.append(f"| {item['path']} | {item['reasons']} | 迁移到 `05-Inbox`、重命名，或删除占位文件 |")
    if not findings:
        report_lines.append("| - | - | 当前没有识别到明显噪声 |")
    write_text(vault_dir / "03-Admin" / "图谱噪声治理.md", "\n".join(report_lines))
    return {"noise_count": len(findings), "findings": findings}


def main() -> None:
    configure_utf8_stdio()
    args = parse_args()
    course_name = sanitize_name(args.course_name)
    vault_dir = Path(args.vault_dir)
    course_dir = vault_dir / "01-Courses" / course_name
    concept_dir = vault_dir / "02-Concepts" / course_name
    course_dir.mkdir(parents=True, exist_ok=True)
    concept_dir.mkdir(parents=True, exist_ok=True)
    cleanup_graph_growth_notes(course_dir)

    existing_hub_files = sorted(path for path in concept_dir.glob("[0-9][0-9]-*图谱.md"))
    main_graph, hubs = infer_hubs_from_graph_entry(course_name, concept_dir, args.rebuild_graph)
    if hubs and (args.rebuild_graph or not existing_hub_files):
        write_hub_pages(course_name, concept_dir, hubs)
    write_graph_entry(course_name, main_graph, hubs)

    concept_summaries: list[dict[str, Any]] = []
    lesson_summaries: list[dict[str, Any]] = []
    if not args.skip_frontmatter:
        concept_summaries = normalize_concept_frontmatter(course_name, concept_dir, hubs)
        lesson_summaries = normalize_lesson_frontmatter(course_name, course_dir)

    tracker_names: list[str] = []
    if not args.skip_trackers:
        if not lesson_summaries:
            lesson_summaries = normalize_lesson_frontmatter(course_name, course_dir)
        if not concept_summaries:
            concept_summaries = normalize_concept_frontmatter(course_name, concept_dir, hubs)
        tracker_names = update_course_trackers(
            course_name,
            course_dir,
            concept_dir,
            hubs,
            lesson_summaries,
            concept_summaries,
        )

    config_path = course_dir / "course-config.json"
    config = build_course_config(course_name, config_path, hubs, args)

    buaa_sync = {"status": "skipped", "reason": "disabled"}
    if not args.skip_buaa_sync:
        if not lesson_summaries:
            lesson_summaries = normalize_lesson_frontmatter(course_name, course_dir)
        buaa_sync = sync_buaa_replays(config, course_dir, lesson_summaries)

    replay_drafts = {"status": "skipped", "reason": "not_requested"}
    upgraded_replay_rebuilds = {"status": "skipped", "reason": "not_requested"}
    draft_sub_ids = parse_csv(args.draft_replay_sub_ids)
    draft_dates = parse_csv(args.draft_replay_dates)
    if args.rebuild_upgraded_replays and not args.skip_buaa_sync:
        review_candidates = buaa_sync.get("review_candidates", []) if isinstance(buaa_sync, dict) else []
        if not isinstance(review_candidates, list):
            review_candidates = []
        rebuild_sub_ids = auto_rebuildable_review_sub_ids(course_dir, review_candidates)
        if rebuild_sub_ids:
            upgraded_replay_rebuilds = draft_replay_lessons(
                config,
                course_name,
                course_dir,
                rebuild_sub_ids,
                [],
                args.replay_note_mode,
            )
            lesson_summaries = normalize_lesson_frontmatter(course_name, course_dir)
            if not args.skip_buaa_sync:
                buaa_sync = sync_buaa_replays(config, course_dir, lesson_summaries)
            if not args.skip_trackers:
                if not concept_summaries:
                    concept_summaries = normalize_concept_frontmatter(course_name, concept_dir, hubs)
                tracker_names = update_course_trackers(
                    course_name,
                    course_dir,
                    concept_dir,
                    hubs,
                    lesson_summaries,
                    concept_summaries,
                )
        else:
            upgraded_replay_rebuilds = {"status": "skipped", "reason": "no_auto_rebuildable_upgraded_replays"}
    if draft_sub_ids or draft_dates:
        replay_drafts = draft_replay_lessons(
            config,
            course_name,
            course_dir,
            draft_sub_ids,
            draft_dates,
            args.replay_note_mode,
        )
        lesson_summaries = normalize_lesson_frontmatter(course_name, course_dir)
        if not args.skip_buaa_sync:
            buaa_sync = sync_buaa_replays(config, course_dir, lesson_summaries)
        if not args.skip_trackers:
            if not concept_summaries:
                concept_summaries = normalize_concept_frontmatter(course_name, concept_dir, hubs)
            tracker_names = update_course_trackers(
                course_name,
                course_dir,
                concept_dir,
                hubs,
                lesson_summaries,
                concept_summaries,
            )

    tracker_names = unique_keep_order(tracker_names)
    update_course_overview(course_name, course_dir, hubs, tracker_names, "回放同步", "待整理回放")

    noise = {"noise_count": 0, "findings": []}
    if not args.skip_noise_scan:
        noise = scan_noise(vault_dir)

    print(
        json.dumps(
            {
                "status": "ok",
                "course": course_name,
                "hub_count": len(hubs),
                "concept_count": len([item for item in concept_summaries if item["type"] == "concept"]),
                "lesson_count": len(lesson_summaries),
                "buaa_sync": buaa_sync,
                "upgraded_replay_rebuilds": upgraded_replay_rebuilds,
                "replay_drafts": replay_drafts,
                "noise_count": noise["noise_count"],
                "config_path": str(config_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
