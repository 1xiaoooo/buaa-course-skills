#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


def configure_utf8_stdio() -> None:
    for name in ("stdin", "stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def sanitize_name(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\\\|?*]+', "_", name.strip())
    return cleaned or "课程"


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(content, encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vault-dir", required=True)
    parser.add_argument("--course-name", required=True)
    return parser.parse_args()


def main() -> None:
    configure_utf8_stdio()
    args = parse_args()
    vault_dir = Path(args.vault_dir)
    course_name = sanitize_name(args.course_name)
    course_dir = vault_dir / "01-Courses" / course_name
    concepts_dir = vault_dir / "02-Concepts" / course_name

    (course_dir / "课次").mkdir(parents=True, exist_ok=True)
    (course_dir / "概念").mkdir(parents=True, exist_ok=True)
    (course_dir / "资料").mkdir(parents=True, exist_ok=True)
    concepts_dir.mkdir(parents=True, exist_ok=True)

    overview = f"""# {course_name}

## 课程信息

- 学期：
- 教师：
- 教材：
- 考核方式：

## 章节地图

- 图谱入口：[[02-Concepts/{course_name}/{course_name}概念图谱]]
- 第一部分：
- 第二部分：
- 第三部分：

## 课次索引

- 

## 核心概念

- 

## 课程事务

- [[章节完成度]]
- [[已整理课次]]
- [[待回看问题]]
- [[回放同步]]
- [[待整理回放]]
- [[03-Admin/作业总表]]
- [[03-Admin/考试与通知]]
"""

    course_tasks = """# 事务

- [[章节完成度]]
- [[已整理课次]]
- [[待回看问题]]
- [[回放同步]]
- [[待整理回放]]
"""

    graph_entry = f"""# {course_name}概念图谱

## 课程主干入口

- 第一部分图谱
- 第二部分图谱
- 第三部分图谱

## 浏览建议

- 在 Obsidian Graph 里过滤：`path:"02-Concepts/{course_name}"`
- 先搭课程总图谱，再补章节枢纽页
"""

    chapter_progress = """# 章节完成度
| 章节 | 概念数 | 已接入概念页 | 相关课次 | 备注 |
| --- | --- | --- | --- | --- |
"""

    lesson_index = """# 已整理课次
| 日期 | 课次 | 概念数 | 待核对项 |
| --- | --- | --- | --- |
"""

    review_questions = """# 待回看问题
- 当前还没有汇总出的待回看问题。
"""

    replay_sync = """# 回放同步

- 还没有绑定课程页。
"""

    replay_backlog = """# 待整理回放
- 当前还没有待整理回放。
"""

    config = {
        "course_name": course_name,
        "course_page_url": "",
        "student": "",
        "replay_output_dir": "",
        "ignored_replay_dates": [],
        "ignored_replay_sub_ids": [],
        "chapter_hubs": [],
    }

    write_text(course_dir / "00-课程总览.md", overview)
    write_text(course_dir / "事务.md", course_tasks)
    write_text(course_dir / "章节完成度.md", chapter_progress)
    write_text(course_dir / "已整理课次.md", lesson_index)
    write_text(course_dir / "待回看问题.md", review_questions)
    write_text(course_dir / "回放同步.md", replay_sync)
    write_text(course_dir / "待整理回放.md", replay_backlog)
    write_text(course_dir / "course-config.json", json.dumps(config, ensure_ascii=False, indent=2))
    write_text(concepts_dir / f"{course_name}概念图谱.md", graph_entry)

    print(
        json.dumps(
            {
                "status": "ok",
                "course_dir": str(course_dir),
                "concepts_dir": str(concepts_dir),
                "config_path": str(course_dir / "course-config.json"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
