from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "maintain_obsidian_course.py"
SPEC = importlib.util.spec_from_file_location("maintain_obsidian_course", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC is not None and SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

REVIEW_MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "review_final_note.py"
REVIEW_SPEC = importlib.util.spec_from_file_location("review_final_note_obsidian", REVIEW_MODULE_PATH)
REVIEW_MODULE = importlib.util.module_from_spec(REVIEW_SPEC)
assert REVIEW_SPEC is not None and REVIEW_SPEC.loader is not None
sys.modules[REVIEW_SPEC.name] = REVIEW_MODULE
REVIEW_SPEC.loader.exec_module(REVIEW_MODULE)


class MaintainObsidianCourseTests(unittest.TestCase):
    def test_ensure_course_workspace_creates_course_scaffold(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            vault_dir = Path(tmpdir)
            course_dir, concept_dir = MODULE.ensure_course_workspace(vault_dir, "测试课程")
            self.assertTrue((course_dir / "课次").exists())
            self.assertTrue((course_dir / "概念").exists())
            self.assertTrue((course_dir / "资料").exists())
            self.assertTrue(concept_dir.exists())

    def test_clean_outline_line_filters_english_noise(self) -> None:
        self.assertEqual(MODULE.clean_outline_line("Mathematical Sciences BUAA"), "")
        self.assertEqual(MODULE.clean_outline_line("2026-03-10 08:00"), "")

    def test_clean_outline_line_keeps_meaningful_chinese(self) -> None:
        self.assertEqual(MODULE.clean_outline_line(" 系统设计简介"), "系统设计简介")

    def test_compact_outline_groups_merges_weak_short_groups(self) -> None:
        groups = [
            {"heading": "课程定位与方法入口", "start_sec": 0, "end_sec": 120, "points": ["课程简介"], "slides": ["a.jpg"]},
            {"heading": "例", "start_sec": 120, "end_sec": 135, "points": [], "slides": ["b.jpg"]},
        ]
        compacted = MODULE.compact_outline_groups(groups)
        self.assertEqual(len(compacted), 1)
        self.assertIn("例", compacted[0]["points"])

    def test_transcript_coverage_info_marks_insufficient_long_tail(self) -> None:
        metadata = {"duration": 7200}
        transcript = [{"begin_sec": 0, "end_sec": 600, "text": "前十分钟"}]
        info = MODULE.transcript_coverage_info(metadata, transcript)
        self.assertTrue(info["insufficient"])

    def test_transcript_only_sections_keep_time_chunks_without_raw_samples(self) -> None:
        sections = MODULE.build_transcript_fallback_sections(
            [
                {"begin_sec": 0, "end_sec": 30, "text": "这一部分我们先讨论符号检验和样本中位数的关系"},
                {"begin_sec": 31, "end_sec": 60, "text": "后面再看 Bootstrap 方法在区间估计里的用法"},
            ],
            Path("missing.txt"),
        )
        self.assertTrue(sections)
        self.assertEqual(sections[0]["kind"], "transcript_topic")
        self.assertTrue(all(section["title"].startswith("转写分段") for section in sections))
        self.assertTrue(all(not section.get("points") for section in sections))
        self.assertTrue(all("sample_lines" not in section for section in sections))

    def test_replay_diagnosis_distinguishes_waiting_partial_and_transcript_only(self) -> None:
        waiting = MODULE.build_replay_diagnosis({"duration": 3600}, [], [])
        self.assertEqual(waiting["status"], "waiting_transcript")
        partial = MODULE.build_replay_diagnosis(
            {"duration": 7200},
            [{"begin_sec": 0, "end_sec": 600, "text": "前十分钟"}],
            [],
        )
        self.assertEqual(partial["status"], "partial_transcript")
        transcript_only = MODULE.build_replay_diagnosis(
            {"duration": 1200},
            [{"begin_sec": 0, "end_sec": 1100, "text": "主体内容"}],
            [],
        )
        self.assertEqual(transcript_only["status"], "transcript_only")

    def test_script_does_not_embed_subject_specific_templates(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        banned_phrases = [
            "经典非参数统计的一般流程",
            "非参数统计的基本观念",
            "从样本到参数的基本思路",
            "统计推断的一般过程",
            "U统计量",
            "平稳分布",
            "耦合时刻",
        ]
        for phrase in banned_phrases:
            self.assertNotIn(phrase, source)

    def test_pending_semantic_rebuild_note_is_not_counted_finished(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            course_dir = Path(tmpdir) / "01-Courses" / "测试课程"
            lesson_dir = course_dir / "课次"
            lesson_dir.mkdir(parents=True)
            note_path = lesson_dir / "2026-04-01 第1周星期1 第1,2节.md"
            MODULE.write_text(
                note_path,
                """---
course: 测试课程
title: 2026-04-01 第1周星期1 第1,2节
date: 2026-04-01
source: buaa-replay-rebuild
replay_sub_id: "123"
has_semantic_rebuild_packet: true
rebuild_mode: final-explained
---

# BUAA 课程回放重建纪要
""",
            )
            summaries = MODULE.normalize_lesson_frontmatter("测试课程", course_dir)
            self.assertEqual(summaries, [])
            frontmatter, _ = MODULE.extract_frontmatter_and_body(MODULE.read_text(note_path))
            self.assertEqual(frontmatter.get("semantic_rebuild_status"), "required")

    def test_pending_semantic_packet_without_note_is_listed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            course_dir = Path(tmpdir) / "01-Courses" / "测试课程"
            course_dir.mkdir(parents=True)
            replay_output_dir = Path(tmpdir) / "replays"
            semantic_dir = replay_output_dir / "lessons" / "123" / "semantic_rebuild"
            semantic_dir.mkdir(parents=True)
            packet_path = semantic_dir / "semantic_rebuild_input.json"
            MODULE.write_text(
                packet_path,
                json.dumps(
                    {
                        "mode": "final-explained",
                        "lesson_title": "2026-04-01 第1周星期1 第1,2节",
                        "lesson_note_path": "D:/fake/2026-04-01 第1周星期1 第1,2节.md",
                        "metadata": {"date": "2026-04-01", "replay_sub_id": "123"},
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
            )
            pending = MODULE.semantic_rebuild_pending_lessons(course_dir, replay_output_dir)
            self.assertEqual(len(pending), 1)
            self.assertEqual(pending[0]["sub_id"], "123")
            self.assertEqual(pending[0]["mode"], "final-explained")

    def test_needs_review_note_is_not_counted_finished(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            course_dir = Path(tmpdir) / "01-Courses" / "测试课程"
            lesson_dir = course_dir / "课次"
            lesson_dir.mkdir(parents=True)
            note_path = lesson_dir / "2026-04-01 第1周星期1 第1,2节.md"
            MODULE.write_text(
                note_path,
                """---
course: 测试课程
title: 2026-04-01 第1周星期1 第1,2节
date: 2026-04-01
source: buaa-replay-needs-review
replay_diagnosis: needs_review
---

# 需要复查
""",
            )
            summaries = MODULE.normalize_lesson_frontmatter("测试课程", course_dir)
            self.assertEqual(summaries, [])

    def test_course_affairs_write_candidates_without_overwriting_agent_reviewed_digest(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            vault_dir = Path(tmpdir)
            course_dir = vault_dir / "01-Courses" / "测试课程"
            admin_dir = vault_dir / "03-Admin"
            lesson_dir = course_dir / "课次"
            lesson_dir.mkdir(parents=True)
            admin_dir.mkdir(parents=True)
            MODULE.write_text(
                admin_dir / "作业总表.md",
                "# 作业总表\n\n| 课程 | 日期 | 内容 | 截止时间 | 状态 | 备注 |\n| --- | --- | --- | --- | --- | --- |\n| 其他课 | 2026-03-01 | 旧作业 |  | 待核对 |  |\n",
            )
            MODULE.write_text(course_dir / "事务.md", "# 事务\n\n## 作业\n\n- 已由 agent 审核的条目。\n")
            MODULE.write_text(
                admin_dir / "考试与通知.md",
                "# 考试与通知\n\n## 考试\n\n| 课程 | 日期 | 类型 | 范围 | 备注 |\n| --- | --- | --- | --- | --- |\n\n## 通知\n\n| 课程 | 日期 | 内容 | 备注 |\n| --- | --- | --- | --- |\n",
            )
            MODULE.write_text(
                lesson_dir / "2026-04-01 第1周星期1 第1,2节.md",
                """---
course: 测试课程
title: 2026-04-01 第1周星期1 第1,2节
date: 2026-04-01
source: buaa-replay-semantic-rebuild
semantic_rebuild_completed: true
concepts:
  - 概念A
---

# 2026-04-01 第1周星期1 第1,2节

## 课程事务

### 作业

- 本周完成第一章习题，提交方式以后续平台通知为准。
- 当前未从转写中识别出稳定的作业信息。

### 考试

- 期末开卷考试。

### 课程安排

- 下周继续讨论模型选择。

### 通知

- 课程资料会放在北航云盘。
""",
            )
            summaries = MODULE.normalize_lesson_frontmatter("测试课程", course_dir)
            MODULE.update_course_affairs(vault_dir, "测试课程", course_dir, summaries)
            course_affairs = (course_dir / "事务候选.md").read_text(encoding="utf-8")
            reviewed_affairs = (course_dir / "事务.md").read_text(encoding="utf-8")
            assignments = (admin_dir / "作业总表.md").read_text(encoding="utf-8")
            notices = (admin_dir / "考试与通知.md").read_text(encoding="utf-8")
            self.assertIn("本周完成第一章习题", course_affairs)
            self.assertNotIn("当前未从转写中识别出稳定", course_affairs)
            self.assertIn("已由 agent 审核", reviewed_affairs)
            self.assertNotIn("本周完成第一章习题", reviewed_affairs)
            self.assertIn("| 其他课 | 2026-03-01 | 旧作业", assignments)
            self.assertNotIn("测试课程", assignments)
            self.assertNotIn("测试课程", notices)

    def test_extract_affairs_keeps_only_concrete_classroom_affairs(self) -> None:
        body = """## 课堂事务

时间参考：约 `01:00:00`

- 老师提醒作业提交：如果错过平台提交时间，下一次提交时一起补交并说明原因。
- 老师鼓励大家把大问题拆成小问题，代码、推导和资料查阅都可以作为学习过程的一部分。

### 考试

- 考核方式：平时作业 20 分，共 4 次；大作业 30 分；期末开卷考试 50 分。

### 通知

- 课程资料会放在北航云盘，课堂 PPT 中给出的文件夹名是 `StatisticalLearning`。
"""
        affairs = MODULE.extract_lesson_affairs(body)
        self.assertIn("老师提醒作业提交", "\n".join(affairs["作业"]))
        self.assertIn("考核方式", "\n".join(affairs["考试"]))
        self.assertIn("北航云盘", "\n".join(affairs["通知"]))
        self.assertNotIn("拆成小问题", "\n".join(affairs["作业"]))

    def test_metadata_refresh_ignores_missing_ppt_outline(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            lesson_dir = Path(tmpdir)
            metadata_path = lesson_dir / "metadata.json"
            MODULE.write_text(metadata_path, "{}")
            metadata_path.touch()
            stale_time = MODULE.datetime.now().timestamp() - 13 * 3600
            MODULE.os.utime(metadata_path, (stale_time, stale_time))
            metadata = {
                "has_transcript": True,
                "transcript_coverage": {"coverage_ratio": 1.0},
                "ppt_video_url": "https://example.com/ppt.mp4",
            }
            reasons = MODULE.metadata_needs_refresh(metadata_path, metadata, require_ppt_outline=True)
            self.assertEqual(reasons, [])

    def test_auto_rebuildable_upgrade_targets_skip_semantic_notes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            course_dir = Path(tmpdir) / "01-Courses" / "测试课程"
            lesson_dir = course_dir / "课次"
            lesson_dir.mkdir(parents=True)
            generated = lesson_dir / "2026-04-01 第1周星期1 第1,2节.md"
            semantic = lesson_dir / "2026-04-08 第2周星期1 第1,2节.md"
            MODULE.write_text(generated, "---\nsource: buaa-replay-rebuild\n---\n")
            MODULE.write_text(semantic, "---\nsource: buaa-replay-semantic-rebuild\nsemantic_rebuild_completed: true\n---\n")
            targets = MODULE.auto_rebuildable_review_sub_ids(
                course_dir,
                [
                    {"date": "2026-04-01", "sub_title": "第1周星期1第1,2节", "sub_id": "111"},
                    {"date": "2026-04-08", "sub_title": "第2周星期1第1,2节", "sub_id": "222"},
                ],
            )
            self.assertEqual(targets, ["111"])

    def test_semantic_graph_bootstrap_is_disabled_for_simplified_flow(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            vault_dir = Path(tmpdir)
            course_dir = vault_dir / "01-Courses" / "测试课程"
            course_dir.mkdir(parents=True)
            config = {"chapter_hubs": []}
            hubs, concepts, info = MODULE.ensure_semantic_graph_bootstrap(
                "测试课程",
                course_dir,
                config,
                "2026-04-01 第1周星期1 第1,2节",
                ["先验分布", "后验分布", "共轭先验"],
                "final-explained",
            )
            self.assertFalse(info["created"])
            self.assertEqual(info["reason"], "bootstrap_disabled")
            self.assertEqual(hubs, [])
            self.assertEqual(concepts, [])
            self.assertFalse((vault_dir / "02-Concepts" / "测试课程").exists())

    def test_semantic_prompt_requires_time_axis_and_avoids_candidate_lists(self) -> None:
        prompt = MODULE.build_semantic_rebuild_prompt("final-explained")
        self.assertIn("每个分段都必须保留时间轴", prompt)
        self.assertIn("time_range", prompt)
        self.assertIn("面向学生的完成稿", prompt)
        self.assertIn("考试、作业、截止时间", prompt)
        self.assertIn("先判断课程领域", prompt)
        self.assertNotIn("concept_candidates", prompt)
        self.assertNotIn("graph_bootstrap", prompt)

    def test_user_visible_transcript_topic_content_hides_internal_anchors(self) -> None:
        section = {
            "kind": "transcript_topic",
            "role": "lecture",
            "title": "转写分段2：应该是 / 分布组",
            "display_index": 2,
            "points": ["应该是", "分布组"],
            "sample_lines": ["这个原来也算稳", "好 再过来说说在36页下面"],
        }
        self.assertEqual(MODULE.display_section_title(section), "课堂讲解与主题推进 2")
        bullets = MODULE.render_final_section_bullets(section, ["老师继续解释后验分布和模型比较"])
        joined = "\n".join(bullets)
        self.assertNotIn("主题候选", joined)
        self.assertNotIn("代表性表达", joined)
        self.assertNotIn("应该是", joined)
        self.assertNotIn("分布组", joined)

    def test_transcript_overview_hides_raw_sample_lines(self) -> None:
        overview = MODULE.transcript_overview_payload(
            [
                {"text": "老师先解释后验分布和先验分布的关系"},
                {"text": "然后说明条件独立在模型里的作用"},
            ]
        )
        self.assertIn("segment_count", overview)
        self.assertIn("evidence_policy", overview)
        self.assertNotIn("sample_lines", overview)
        self.assertNotIn("topic_phrases", overview)

    def test_review_final_note_packet_preserves_current_file_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            note = root / "lesson.md"
            transcript = root / "transcript.txt"
            semantic_input = root / "semantic_rebuild_input.json"
            review_dir = root / "review"
            note.write_text("# 测试课次\n\n## 内容纪要\n\n00:00-08:00 老师说明了任务要求和课堂练习。\n", encoding="utf-8")
            transcript.write_text("老师说明了任务要求，并让同学进行课堂练习。", encoding="utf-8")
            semantic_input.write_text(
                json.dumps(
                    {
                        "references": {"transcript": str(transcript)},
                        "course_name": "测试课程",
                        "lesson_title": "测试课次",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            old_argv = sys.argv[:]
            sys.argv = [
                "review_final_note.py",
                "--note",
                str(note),
                "--semantic-input",
                str(semantic_input),
                "--output-dir",
                str(review_dir),
            ]
            try:
                self.assertEqual(REVIEW_MODULE.main(), 0)
            finally:
                sys.argv = old_argv
            review_input = json.loads((review_dir / "final_note_review_input.json").read_text(encoding="utf-8"))
            prompt = (review_dir / "final_note_review_prompt.md").read_text(encoding="utf-8")
            self.assertEqual(review_input["note"]["sha256"], REVIEW_MODULE.sha256_file(note))
            self.assertTrue(review_input["hard_gate"]["passed"])
            self.assertIn("旧审查结论失效", prompt)
            self.assertIn("提前下课、自主做题、课堂展示", prompt)


if __name__ == "__main__":
    unittest.main()
