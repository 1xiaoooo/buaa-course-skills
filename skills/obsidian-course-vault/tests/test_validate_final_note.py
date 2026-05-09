from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "validate_final_note.py"
SPEC = importlib.util.spec_from_file_location("validate_final_note_obsidian", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC is not None and SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class ValidateFinalNoteTests(unittest.TestCase):
    def test_accepts_monotone_replay_timestamp_timeline(self) -> None:
        text = """
## 内容纪要

### 开始

时间参考：约 `03:29-18:58`

### 推进

时间参考：约 `49:40-01:05:29`
"""
        self.assertEqual(MODULE.validate_timeline_markers(text), [])

    def test_rejects_ambiguous_short_hour_like_ranges(self) -> None:
        text = "时间参考：约 `01:20-01:39`"
        issues = MODULE.validate_timeline_markers(text)
        self.assertTrue(any("too short" in issue for issue in issues))

    def test_rejects_timeline_that_moves_backward(self) -> None:
        text = """
时间参考：约 `30:00-45:00`
时间参考：约 `20:00-35:00`
"""
        issues = MODULE.validate_timeline_markers(text)
        self.assertTrue(any("moves backward" in issue for issue in issues))

    def test_rejects_non_timestamp_timeline_range(self) -> None:
        text = "时间参考：约 `第 10-20 分钟`"
        issues = MODULE.validate_timeline_markers(text)
        self.assertTrue(any("not a replay timestamp range" in issue for issue in issues))


if __name__ == "__main__":
    unittest.main()
