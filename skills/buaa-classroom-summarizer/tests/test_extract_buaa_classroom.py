from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "extract_buaa_classroom.py"
SPEC = importlib.util.spec_from_file_location("extract_buaa_classroom", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC is not None and SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class ExtractBuaaClassroomTests(unittest.TestCase):
    def test_semantic_prompt_uses_transcript_first_rules(self) -> None:
        prompt = MODULE.build_semantic_rebuild_prompt("final-explained")
        self.assertIn("课程转写永远是唯一主来源", prompt)
        self.assertIn("不要让 PPT 决定 section 边界", prompt)
        self.assertIn("每个分段都必须保留时间轴", prompt)
        self.assertNotIn("concept_candidates", prompt)

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

    def test_transcript_only_sections_keep_transcript_chunks_without_points(self) -> None:
        sections = MODULE.build_transcript_fallback_sections(
            [
                {"begin_sec": 0, "end_sec": 30, "text": "这一部分我们先讨论符号检验和样本中位数的关系"},
                {"begin_sec": 31, "end_sec": 60, "text": "后面再看 Bootstrap 方法在区间估计里的用法"},
            ],
            "",
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

    def test_transcript_overview_contains_only_sample_lines(self) -> None:
        overview = MODULE.transcript_overview_payload(
            [
                {"text": "老师先解释后验分布和先验分布的关系"},
                {"text": "然后说明条件独立在模型里的作用"},
            ]
        )
        self.assertIn("sample_lines", overview)
        self.assertNotIn("topic_phrases", overview)

    def test_user_visible_transcript_topic_content_hides_internal_anchors(self) -> None:
        section = {
            "kind": "transcript_topic",
            "role": "lecture",
            "title": "转写分段2",
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

    def test_semantic_markdown_mode_writes_packet_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            note_path = output_dir / "lesson_note.md"
            metadata = {
                "course_title": "测试课程",
                "date": "2026-04-01",
                "sub_title": "第1周星期1 第1,2节",
                "sub_id": "123",
                "source_url": "https://example.com/livingroom",
                "preferred_stream": "teacher",
                "duration": 600,
                "transcript_coverage": {"coverage_ratio": 1.0, "insufficient": False},
                "replay_diagnosis": "transcript_only",
            }
            transcript_segments = [
                {"begin_sec": 0, "end_sec": 60, "text": "老师先解释后验分布和先验分布的关系"},
                {"begin_sec": 61, "end_sec": 120, "text": "后面继续说明条件独立在模型里的位置"},
            ]
            result = MODULE.export_markdown_note(
                output_dir,
                metadata,
                transcript_segments,
                "老师先解释后验分布和先验分布的关系\n后面继续说明条件独立在模型里的位置",
                note_path,
                "final-explained",
            )
            self.assertEqual(result["status"], "pending_semantic")
            self.assertFalse(note_path.exists())
            packet_path = Path(result["semantic_rebuild_input"])
            self.assertTrue(packet_path.exists())
            packet = json.loads(packet_path.read_text(encoding="utf-8"))
            self.assertNotIn("seed_mainline", packet)
            self.assertNotIn("concept_candidates", packet)


if __name__ == "__main__":
    unittest.main()
