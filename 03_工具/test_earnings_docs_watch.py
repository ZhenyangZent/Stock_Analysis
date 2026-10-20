import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import earnings_docs_watch as edw


class EarningsDocsWatchTest(unittest.TestCase):
    def test_parse_submission_documents(self):
        raw = (b"<DOCUMENT>\n<TYPE>10-Q\n<FILENAME>x.htm\n<TEXT><html>Q</html></TEXT>\n</DOCUMENT>"
               b"<DOCUMENT>\n<TYPE>EX-99.1\n<FILENAME>e.htm\n<TEXT><html>Financial Results</html></TEXT>\n</DOCUMENT>")
        docs = edw.parse_submission_documents(raw)
        self.assertEqual([(x["type"], x["filename"]) for x in docs], [("10-Q", "x.htm"), ("EX-99.1", "e.htm")])
        self.assertEqual([x["filename"] for x in edw._earnings_exhibits(docs)], ["e.htm"])

    def test_queue_only_contains_registered_docs(self):
        queue = edw._queue([{"issuer_id": "ISSUER_X", "doc_id": "DOC_X", "report_period_end": "2026-09-30"}],
                           "2026-10-20T00:00:00+00:00")
        self.assertEqual(len(queue["companies"]), 1)
        self.assertEqual(queue["companies"][0]["new_report_doc_ids"], ["DOC_X"])
        self.assertEqual(queue["companies"][0]["b_window"]["model_family"], "OPENAI")
        self.assertEqual(edw._queue([], "2026-10-20T00:00:00+00:00")["companies"], [])

    def test_tsm_candidate_is_never_registered(self):
        recent = [{"form": "6-K", "accessionNumber": "0001046179-26-000726", "reportDate": "2026-09-30",
                   "acceptanceDateTime": "20261015103000"}]
        submission = [{"type": "EX-99.1", "filename": "earnings.htm", "description": "earnings",
                       "raw": b"<html>Third Quarter Financial Results</html>"}]
        with mock.patch.object(edw, "_submission", return_value=("https://sec.test/base", b"", submission)), \
             mock.patch.object(edw.ai_fields, "active_field_values", return_value=[]), \
             mock.patch.object(edw, "_write_candidate_raw", return_value="a" * 64), \
             mock.patch.object(edw.ai_fields, "register_doc") as register:
            got = edw._tsm_candidate({"issuer_id": "ISSUER_TSMC", "cik": "0001046179"}, recent, [],
                                     "2026-10-20T00:00:00+00:00", True)
        self.assertEqual(got["status"], "CANDIDATE_NOT_ACTIVE")
        register.assert_not_called()

    def test_weekly_refresh_invokes_live_detector(self):
        source = Path(os.path.dirname(__file__), "weekly_refresh.py").read_text(encoding="utf-8")
        self.assertIn('"earnings_docs_watch.py"', source)
        self.assertIn('"--now", "--lock-holder", holder', source)


if __name__ == "__main__":
    unittest.main()
