from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "collect_buaa_course_replays.py"
SPEC = importlib.util.spec_from_file_location("collect_buaa_course_replays", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC is not None and SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class CollectBuaaCourseReplaysTests(unittest.TestCase):
    def test_parse_url_extracts_query_params(self) -> None:
        path, params = MODULE.parse_url(
            "https://classroom.msa.buaa.edu.cn/coursedetail?course_id=136814&tenant_code=21&username=23377213"
        )
        self.assertEqual(path, "/coursedetail")
        self.assertEqual(params["course_id"], "136814")
        self.assertEqual(params["tenant_code"], "21")
        self.assertEqual(params["username"], "23377213")

    def test_compare_snapshots_detects_new_replay(self) -> None:
        previous = {
            "checked_at": "2026-04-10T10:00:00",
            "lessons": [
                {"sub_id": "1", "replay_ready": False},
                {"sub_id": "2", "replay_ready": True},
            ],
        }
        current = {
            "course_id": "136814",
            "checked_at": "2026-04-11T10:00:00",
            "replay_ready_lessons": 2,
            "lessons": [
                {"sub_id": "1", "date": "2026-04-10", "sub_title": "A", "livingroom_url": "u1", "replay_ready": True},
                {"sub_id": "2", "date": "2026-04-11", "sub_title": "B", "livingroom_url": "u2", "replay_ready": True},
            ],
        }
        result = MODULE.compare_snapshots(previous, current)
        self.assertEqual(result["new_replay_count"], 1)
        self.assertEqual(result["new_replays"][0]["sub_id"], "1")

    def test_classify_lesson_extract_reads_replay_diagnosis(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            lesson_dir = Path(tmpdir)
            (lesson_dir / "metadata.json").write_text(
                json.dumps(
                    {
                        "replay_diagnosis": "partial_transcript",
                        "has_transcript": True,
                        "transcript_coverage": {"coverage_ratio": 0.42},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            result = MODULE.classify_lesson_extract(lesson_dir)
            self.assertEqual(result["status"], "partial_transcript")
            self.assertTrue(result["has_transcript"])
            self.assertAlmostEqual(result["coverage_ratio"], 0.42)

if __name__ == "__main__":
    unittest.main()
