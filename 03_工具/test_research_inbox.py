"""T38 研究收件匣測試：全部使用暫存目錄，不碰專案帳本。"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

MODULE_PATH = Path(__file__).with_name("research_inbox.py")
SPEC = importlib.util.spec_from_file_location("research_inbox", MODULE_PATH)
ri = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ri)


class ResearchInboxTest(unittest.TestCase):
    def setUp(self):
        # 放在測試檔旁，避免受限執行環境的系統 Temp 目錄不可寫。
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.root = Path(self.tmp.name) / "05_研究收件匣"
        self.root.mkdir()
        (self.root / "inbox.jsonl").write_bytes(b"")
        self.public = {"source_id": "pub", "name": "Public", "type": "youtube", "url": "https://example.com",
                       "language": "zh-Hant", "member_only": False, "enabled": True}
        self.member = dict(self.public, source_id="member", member_only=True)

    def tearDown(self):
        self.tmp.cleanup()

    def _input(self, name="item"):
        folder = Path(self.tmp.name) / "yt"; folder.mkdir(exist_ok=True)
        info = folder / f"{name}.info.json"
        info.write_text(json.dumps({"title": name, "webpage_url": f"https://youtu.be/{name}", "upload_date": "20261019"}), encoding="utf-8")
        subtitle = folder / f"{name}.zh-Hant.vtt"
        subtitle.write_text("WEBVTT\n\n00:00:00.000 --> 00:00:01.000\n公開內容\n", encoding="utf-8")
        return info, subtitle

    def test_member_content_is_referenced_not_copied(self):
        info, subtitle = self._input("members")
        status = ri.add_record(self.root, self.member, title="members", original_url="https://youtu.be/members",
                               published_at=None, content_bytes=info.read_bytes(), transcript_path=subtitle)
        self.assertEqual(status, "added")
        row = ri.read_jsonl(self.root / "inbox.jsonl")[0]
        self.assertIsNone(row["transcript"])
        self.assertEqual(row["external_content"]["path"], str(subtitle.resolve()))
        self.assertEqual(row["usage_mark"], "MEMBER_ONLY_PERSONAL_USE")
        self.assertFalse((self.root / "transcripts").exists())

    def test_formal_fields_and_outside_paths_are_blocked(self):
        outside = Path(self.tmp.name) / "01_共用資料" / "data_spine" / "ai_field_values.jsonl"
        with self.assertRaisesRegex(ValueError, "PATH_OUTSIDE_ROOT"):
            ri.ensure_inside(self.root, outside)
        with self.assertRaisesRegex(ValueError, "FORMAL_FIELD_WRITE_FORBIDDEN:C5"):
            ri.append_jsonl(self.root, "inbox.jsonl", {"field": "C5", "value": "有"})
        self.assertFalse(outside.exists())

    def test_same_content_is_registered_once(self):
        info, subtitle = self._input("same")
        kwargs = dict(title="same", original_url="https://youtu.be/same", published_at=None,
                      content_bytes=info.read_bytes(), transcript_path=subtitle)
        self.assertEqual(ri.add_record(self.root, self.public, **kwargs), "added")
        self.assertEqual(ri.add_record(self.root, self.public, **kwargs), "duplicate")
        self.assertEqual(len(ri.read_jsonl(self.root / "inbox.jsonl")), 1)

    def test_inbox_is_append_only(self):
        first_info, first_subtitle = self._input("first")
        ri.add_record(self.root, self.public, title="first", original_url="https://youtu.be/first",
                      published_at=None, content_bytes=first_info.read_bytes(), transcript_path=first_subtitle)
        before = (self.root / "inbox.jsonl").read_bytes()
        second_info, second_subtitle = self._input("second")
        ri.add_record(self.root, self.public, title="second", original_url="https://youtu.be/second",
                      published_at=None, content_bytes=second_info.read_bytes(), transcript_path=second_subtitle)
        after = (self.root / "inbox.jsonl").read_bytes()
        self.assertTrue(after.startswith(before))
        self.assertGreater(len(after), len(before))
        self.assertEqual(len(ri.read_jsonl(self.root / "inbox.jsonl")), 2)

    def test_public_transcript_is_normalized_and_question_events_append(self):
        info, subtitle = self._input("public")
        ri.add_record(self.root, self.public, title="public", original_url="https://youtu.be/public",
                      published_at=None, content_bytes=info.read_bytes(), transcript_path=subtitle)
        row = ri.read_jsonl(self.root / "inbox.jsonl")[0]
        saved = self.root / row["transcript"]["path"]
        self.assertEqual(saved.read_text(encoding="utf-8"), "公開內容\n")
        self.assertEqual(ri.add_question(self.root, row["record_id"], "需要查官方文件？"), "added")
        question = ri.read_jsonl(self.root / "verification_questions.jsonl")[0]
        before = (self.root / "verification_questions.jsonl").read_bytes()
        ri.update_question(self.root, question["question_id"], "VERIFIED_OFFICIAL", ["DOC_1"])
        after = (self.root / "verification_questions.jsonl").read_bytes()
        self.assertTrue(after.startswith(before))
        self.assertEqual(len(ri.read_jsonl(self.root / "verification_questions.jsonl")), 2)


if __name__ == "__main__":
    unittest.main()
