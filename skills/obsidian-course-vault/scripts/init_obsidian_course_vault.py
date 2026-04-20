#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


HOME_NOTE = """# 课程知识库

## 使用方式

- 在 `01-Courses` 里维护每门课。
- 在 `02-Concepts` 里沉淀跨课次概念。
- 在 `03-Admin` 里汇总作业、考试、通知。
- 在 `05-Inbox` 里放待整理草稿。
- 知识图谱建议只看 `02-Concepts`，并按课程文件夹过滤。
- 长课建议维护章节枢纽页、已整理课次、待回看问题和回放同步页。

## 快速入口

- [[03-Admin/作业总表]]
- [[03-Admin/考试与通知]]
- [[03-Admin/图谱噪声治理]]
- [[04-Templates/课程总览模板]]
- [[04-Templates/课次纪要模板]]
- [[04-Templates/概念模板]]
- [[03-Admin/知识图谱使用规范]]
"""

ASSIGNMENTS_NOTE = """# 作业总表

| 课程 | 日期 | 内容 | 截止时间 | 状态 | 备注 |
| --- | --- | --- | --- | --- | --- |
"""

EXAMS_NOTE = """# 考试与通知

## 考试

| 课程 | 日期 | 类型 | 范围 | 备注 |
| --- | --- | --- | --- | --- |

## 通知

| 课程 | 日期 | 内容 | 备注 |
| --- | --- | --- | --- |
"""

GRAPH_GUIDE = """# 知识图谱使用规范

## 原则

- 知识图谱只承载“概念”。
- 课次笔记、作业、通知不作为图谱中心。
- 一张成熟的课程图谱应当有“章节枢纽”，而不只是平铺概念名。

## 建议做法

- 把概念页集中放在 `02-Concepts/课程名`。
- 每门课至少维护一张总图谱页，再按章节维护若干张“知识地图页”。
- 主要维护概念之间的链接：
  - 前置概念
  - 推导到
  - 相关例子
  - 对比概念
- 概念页正文尽量只保留概念内容，把课程、章节、首次出现课次等信息放进 frontmatter。
- 概念页 frontmatter 建议稳定维护这些字段：
  - `course`
  - `chapter`
  - `first_seen`
  - `prerequisites`
  - `related`
  - `contrasts`
  - `examples`
  - `lesson_refs`
- 课次页可以链接概念页，但不要把课次页当知识图谱主视图。

## 在 Obsidian 里看图谱

- 建议在 Graph 里加过滤：`path:"02-Concepts/课程名"`
- 日常浏览建议隐藏未解析链接与孤立节点。
- 如果全局图谱里出现 `未命名.canvas`、`未命名.base`、空白占位页等草稿，尽快移出 vault 或删除。
"""

NOISE_REPORT = """# 图谱噪声治理

- 当前还没有扫描结果。
- 建议定期检查 `未命名.canvas`、`未命名.base`、空白占位页和模板残留页。
"""

COURSE_TEMPLATE = """# {{course_name}}

## 课程信息

- 学期：
- 教师：
- 教材：
- 考核方式：

## 章节地图

- 图谱入口：
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

LESSON_TEMPLATE = """---
type: lesson
course: {{course_name}}
title: {{lesson_title}}
date:
concepts: []
review_items: []
---

# {{lesson_title}}

## 元信息

- 课程：[[{{course_name}}/00-课程总览]]
- 日期：
- 节次：
- 视频：

## 本节主线

- 

## 内容纪要

### 主题 1

时间参考：

- 

## 课程事务

### 作业

- 

### 考试

- 

### 通知

- 

## 本节提到的概念

- [[概念名]]

## 待核对

- 
"""

CONCEPT_TEMPLATE = """---
type: concept
course: {{course_name}}
title: {{concept_name}}
chapter: []
first_seen:
prerequisites: []
related: []
contrasts: []
examples: []
lesson_refs: []
---

# {{concept_name}}

## 定义

- 

## 直觉理解

- 

## 前置概念

- [[概念名]]

## 推导到 / 关联到

- [[概念名]]

## 易混概念

- [[概念名]]

## 相关公式 / 结论

- 

## 典型例子

- 
"""


def configure_utf8_stdio() -> None:
    for name in ("stdin", "stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(content, encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--obsidian-dir", required=True, help="Directory containing the Obsidian application")
    parser.add_argument("--vault-dir", required=True, help="Directory to initialize as an Obsidian vault")
    return parser.parse_args()


def resolve_obsidian_app(obsidian_dir: Path) -> Path:
    candidates = [
        obsidian_dir / "Obsidian.exe",
        obsidian_dir / "Obsidian.app",
        obsidian_dir / "obsidian",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise SystemExit(f"Obsidian application not found under: {obsidian_dir}")


def main() -> None:
    configure_utf8_stdio()
    args = parse_args()
    obsidian_dir = Path(args.obsidian_dir)
    vault_dir = Path(args.vault_dir)
    obsidian_app = resolve_obsidian_app(obsidian_dir)

    dirs = [
        vault_dir / ".obsidian",
        vault_dir / "01-Courses",
        vault_dir / "02-Concepts",
        vault_dir / "03-Admin",
        vault_dir / "04-Templates",
        vault_dir / "05-Inbox",
    ]
    for item in dirs:
        item.mkdir(parents=True, exist_ok=True)

    write_text(vault_dir / "00-Home.md", HOME_NOTE)
    write_text(vault_dir / "03-Admin" / "作业总表.md", ASSIGNMENTS_NOTE)
    write_text(vault_dir / "03-Admin" / "考试与通知.md", EXAMS_NOTE)
    write_text(vault_dir / "03-Admin" / "知识图谱使用规范.md", GRAPH_GUIDE)
    write_text(vault_dir / "03-Admin" / "图谱噪声治理.md", NOISE_REPORT)
    write_text(vault_dir / "04-Templates" / "课程总览模板.md", COURSE_TEMPLATE)
    write_text(vault_dir / "04-Templates" / "课次纪要模板.md", LESSON_TEMPLATE)
    write_text(vault_dir / "04-Templates" / "概念模板.md", CONCEPT_TEMPLATE)

    meta = {
        "obsidian_app": str(obsidian_app),
        "vault_dir": str(vault_dir),
        "initialized_by": "obsidian-course-vault skill",
    }
    write_text(vault_dir / ".obsidian" / "course-vault.json", json.dumps(meta, ensure_ascii=False, indent=2))

    print(json.dumps({"status": "ok", **meta}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
