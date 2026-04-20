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


class MaintainObsidianCourseTests(unittest.TestCase):
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

    def test_transcript_only_sections_keep_transcript_chunks_without_points(self) -> None:
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
        self.assertTrue(any(section.get("sample_lines") for section in sections))

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

    def test_transcript_overview_contains_only_sample_lines(self) -> None:
        overview = MODULE.transcript_overview_payload(
            [
                {"text": "老师先解释后验分布和先验分布的关系"},
                {"text": "然后说明条件独立在模型里的作用"},
            ]
        )
        self.assertIn("sample_lines", overview)
        self.assertNotIn("topic_phrases", overview)


if __name__ == "__main__":
    unittest.main()
