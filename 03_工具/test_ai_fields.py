"""S4a：ai_fields.py 的測試（合成文件、暫存目錄、暫存鎖；不碰專案的帳本與鎖）。
執行：python 03_工具/test_ai_fields.py"""
import copy
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ai_fields as af
import lock as project_lock

ISSUER = "ISSUER_TEST"
TEXTS = {
    "OLD": "Quarterly report for the period ended December 31, 2025. AI related products are still in development and have not generated revenue.",
    "R1": "Quarterly report for the quarter ended March 31, 2026. Revenue was $100 million. Our networking segment revenue was $40 million.",
    "R2": ("Quarterly report for the quarter ended June 30, 2026. AI accelerator revenue was $55 million this quarter. "
           "Segment revenue for data center products was $70 million, and the increase was primarily driven by AI demand. "
           "Growth was driven mainly by AI demand across data center customers. "
           "Demand has driven increased demand for networking as AI deployment accelerates. "
           "Segment revenue increased 120% primarily due to increases in bit shipments and average selling prices driven by accelerating AI demand in cloud markets. "
           "AI is discussed throughout this report. Our largest customers are hyperscalers. "
           "Management expects the primarily seasonal pattern to continue. "
           "Our AI products have not yet generated revenue and are in validation. "
           "Ｔｈｅ  customer   concentration is high.  資料中心需求強勁，雲端客戶持續擴張。"
           "Revenue from the acquired Beta business was $30 million in the quarter. "
           "One-time license revenue of $25 million was recognized in the quarter. "
           "There were no other material acquisitions during the period. "
           "本季併購之子公司貢獻營收新台幣 3,000 萬元。"
           "Changes in Net sales: Organic growth 14 % Acquisitions of businesses 7 % Total increase in Net sales 21 %"),
    "CALL": "Earnings call presentation for the quarter ended June 30, 2026. Management discussed gross margin and inventory.",
}
TEXTS["R1"] += " We expect full-year revenue of $10 billion to $12 billion. We expect 2026 capital expenditures of $40 billion to $40 billion."
TEXTS["R2"] += (" We now expect full-year revenue of $11.2 billion to $13.2 billion. "
                 "Our unchanged scenario is full-year revenue of $10.89 billion to $11.11 billion. "
                 "We now expect 2026 capital expenditures of $42 billion to $42 billion. "
                 "We are maintaining our prior capital expenditure outlook for 2026.")
# T19：G-S4-3a（文字型明確修正）與 G-S4-5a（資本支出定義改變）用的合成句
TEXTS["R1"] += " We now expect our full-year 2026 revenue growth to be slightly above 40% year over year."
TEXTS["R2"] += (" We are raising our full-year 2026 revenue growth outlook to the mid-40s. "
                 "We expect strong revenue growth in full-year 2026. "
                 "We are raising our revenue growth outlook to the mid-40s. "
                 "We are lowering our full-year 2026 revenue growth outlook to the mid-30s. "
                 "The shift from finance to operating leases adjusts our 2026 capital expenditure expectation to approximately $38 billion. "
                 "Finance leases are included in capital expenditures while operating leases are not. "
                 "On a comparable basis including finance leases, we expect 2026 capital expenditures of $43 billion to $43 billion.")
# T19b：G-S4-3b（字詞表）與 G-S4-5b（同一定義下的文字說法）用的合成句
TEXTS["R2"] += (" We raised our 2026 outlook on lower costs. "
                 "We raised capital in 2026 to fund expansion. "
                 "We now expect our full-year 2026 revenue growth to be around 44%. "
                 "Outside of this lease classification impact, our 2026 capital expenditure expectations remain unchanged. "
                 "Our 2026 capital expenditure expectations remain unchanged.")


# T37：G-S4-3c（I2Q）用的合成句。R1 是「前次財報」，R2 是「剛公布這一季的財報」。
TEXTS["R1"] += " For the current quarter we expect revenue between $90 million and $100 million."
TEXTS["R1"] += " Alternatively net revenue in the range of $960 thousand to $1.01 million."
TEXTS["R2"] += (" Revenue for the quarter was $105 million."
                " For next quarter we expect revenue between $108 million and $118 million."
                " For next quarter we expect revenue between $101 million and $105 million."
                " For next quarter we expect revenue between $98 million and $102 million."
                " For next quarter we expect revenue of approximately $108 million."
                " For next quarter we expect revenue of $100 million +/- 5%."
                " For next quarter we expect revenue of $110 million ± $10 million.")


def make_doc(doc_id, doc_type, published, period=None, report_period=None, issuer=ISSUER, folder=None, key=None):
    text = TEXTS[key or doc_id]
    path = os.path.join(folder, doc_id + ".txt")
    with open(path, "wb") as f: f.write(text.encode("utf-8"))
    return {"doc_id": doc_id, "issuer_id": issuer, "doc_type": doc_type, "url": f"https://example.test/{doc_id}",
            "official_published_at": published, "fetched_at": published, "available_at": published,
            "raw_sha256": "0" * 64, "text_sha256": af.sha256_bytes(text.encode("utf-8")),
            "text_path": os.path.relpath(path, af.ROOT) if path.startswith(af.ROOT) else path, "period_end": period,
            "report_period_end": report_period or period, "robots_status": "TEST"}


class AiFieldsBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.old_root = af.ROOT
        self.old_paths = (af.DOCS_LEDGER, af.EXTRACTIONS, af.FIELD_VALUES, af.RAW_DOCS, af.REVIEWS,
                          af.FIELD_VALUE_VOIDS, af.ENTITY_REGISTRY, af.RULES_JSON)
        self.old_lock = (project_lock.LOCK, project_lock.HIST)
        af.ROOT = self.tmp
        af.DOCS_LEDGER, af.EXTRACTIONS, af.FIELD_VALUES = (os.path.join(self.tmp, n) for n in ("docs.jsonl", "ex.jsonl", "val.jsonl"))
        af.RAW_DOCS = os.path.join(self.tmp, "raw")
        af.REVIEWS = os.path.join(self.tmp, "reviews.jsonl")
        af.FIELD_VALUE_VOIDS = os.path.join(self.tmp, "voids.jsonl")
        # 暫存登錄表：scope 防呆只認這裡的發行人與主線
        af.ENTITY_REGISTRY, af.RULES_JSON = os.path.join(self.tmp, "registry.json"), os.path.join(self.tmp, "rules.json")
        with open(af.ENTITY_REGISTRY, "w", encoding="utf-8") as f:
            json.dump({"issuers": [{"issuer_id": ISSUER}, {"issuer_id": "ISSUER_OTHER"}]}, f)
        with open(af.RULES_JSON, "w", encoding="utf-8") as f:
            json.dump({"themes": {"theme_test": {"watchlist": []}}}, f)
        self.real_ledgers = {n: os.path.exists(os.path.join(self.old_root, "01_共用資料", "data_spine", n))
                             for n in ("docs_ledger.jsonl", "ai_extractions.jsonl", "ai_field_values.jsonl", "ai_reviews.jsonl",
                                       "ai_field_value_voids.jsonl")}
        project_lock.LOCK, project_lock.HIST = os.path.join(self.tmp, "p.lock"), os.path.join(self.tmp, "hist")
        os.makedirs(af.RAW_DOCS)
        self.docs = [
            make_doc("OLD", "10-Q", "2026-02-01T00:00:00+00:00", "2025-12-31", folder=af.RAW_DOCS),
            make_doc("R1", "10-Q", "2026-05-01T00:00:00+00:00", "2026-03-31", folder=af.RAW_DOCS),
            make_doc("R2", "10-Q", "2026-08-01T00:00:00+00:00", "2026-06-30", folder=af.RAW_DOCS),
            make_doc("CALL", "IR-PRESENTATION", "2026-08-02T00:00:00+00:00", folder=af.RAW_DOCS),
        ]

    def tearDown(self):
        af.ROOT = self.old_root
        (af.DOCS_LEDGER, af.EXTRACTIONS, af.FIELD_VALUES, af.RAW_DOCS, af.REVIEWS,
         af.FIELD_VALUE_VOIDS, af.ENTITY_REGISTRY, af.RULES_JSON) = self.old_paths
        # 測試不得碰專案的帳本（尤其是不存在的 ai_reviews.jsonl 不可被建立）
        for n, existed in self.real_ledgers.items():
            self.assertEqual(os.path.exists(os.path.join(self.old_root, "01_共用資料", "data_spine", n)), existed, n)
        project_lock.LOCK, project_lock.HIST = self.old_lock
        shutil.rmtree(self.tmp, ignore_errors=True)

    def ex(self, quotes=None, facts=None, checked=("R1", "R2", "CALL"), extractor="A", docs=("R1", "R2", "CALL")):
        return {"field": "C6", "issuer_id": ISSUER, "target_period": "2026-06-30", "doc_ids": list(docs),
                "checklist": [{"doc_id": d, "checked": True} for d in checked], "quotes": quotes or [], "facts": facts or [],
                "extractor_id": extractor}

    @staticmethod
    def q(doc, typ, text): return {"doc_id": doc, "type": typ, "quote": text}


class EffectivePeriod(AiFieldsBase):
    def test_effective_period_is_latest_two_reports(self):
        e = af.effective_docs(ISSUER, self.docs)
        self.assertEqual(e["reports"], ["2026-06-30", "2026-03-31"])
        self.assertEqual(sorted(e["doc_ids"]), ["CALL", "R1", "R2"])      # OLD 在有效期間之前

    def test_single_report_has_no_effective_period(self):
        self.assertEqual(af.effective_docs(ISSUER, self.docs[2:])["status"], "INSUFFICIENT_REPORTS")


class QuoteVerification(AiFieldsBase):
    GOOD = "AI accelerator revenue was $55 million this quarter"

    def test_exact_quote_passes_and_numbers_checked(self):
        ex = self.ex([self.q("R2", "AI_NUMERIC", self.GOOD)], [{"name": "ai_revenue", "value": 55, "unit": "USD million", "quote_index": 0}])
        self.assertEqual(af.validate_extraction(ex, self.docs), [])

    def test_tampered_quote_fails(self):
        for bad in ("AI accelerator revenue was $65 million this quarter",       # 改數字
                    "AI accelerator revenue was $55 billion this quarter",       # 改單位
                    "AI accelerator revenue was about $55 million this quarter"):  # 插字
            ex = self.ex([self.q("R2", "AI_NUMERIC", bad)])
            self.assertIn("QUOTE_NOT_IN_SOURCE:0", af.validate_extraction(ex, self.docs), bad)
        with self.assertRaisesRegex(ValueError, "QUOTE_NOT_IN_SOURCE"):
            project_lock.acquire("t", "x")
            af.append_extraction(self.ex([self.q("R2", "AI_NUMERIC", "AI accelerator revenue was $65 million this quarter")]), "p", self.docs)

    def test_quote_must_come_from_the_cited_document(self):
        ex = self.ex([self.q("R1", "AI_NUMERIC", self.GOOD)])               # 句子在 R2，不在 R1
        self.assertIn("QUOTE_NOT_IN_SOURCE:0", af.validate_extraction(ex, self.docs))

    def test_nfkc_and_whitespace_normalisation(self):
        self.assertTrue(af.quote_in_text("The customer concentration is high.", TEXTS["R2"]))   # 全形字母與多重空白
        self.assertFalse(af.quote_in_text("short", "short text"))                            # 少於 12 字元

    def test_fact_value_must_appear_in_quote(self):
        ex = self.ex([self.q("R2", "AI_NUMERIC", self.GOOD)], [{"name": "x", "value": 99, "unit": "USD million", "quote_index": 0}])
        self.assertIn("FACT_VALUE_NOT_IN_QUOTE:0", af.validate_extraction(ex, self.docs))

    def test_numeric_types_need_a_number_and_ai_types_need_ai_term(self):
        no_number = self.ex([self.q("R2", "AI_NUMERIC", "Our AI products have not yet generated revenue")])
        self.assertIn("QUOTE_HAS_NO_NUMBER:0", af.validate_extraction(no_number, self.docs))
        no_ai = self.ex([self.q("R2", "AI_MAIN_DRIVER_NO_NUMBER", "資料中心需求強勁，雲端客戶持續擴張")])
        self.assertIn("QUOTE_HAS_NO_AI_TERM:0", af.validate_extraction(no_ai, self.docs))

    def test_segment_driver_needs_a_main_cue(self):
        ex = self.ex([self.q("R2", "SEGMENT_AI_DRIVEN", "Management discussed AI related product plans")])
        # 引句不在原文 + 無主因字樣
        self.assertIn("QUOTE_NOT_IN_SOURCE:0", af.validate_extraction(ex, self.docs))
        ok = self.ex([self.q("R2", "SEGMENT_AI_DRIVEN", "the increase was primarily driven by AI demand")])
        self.assertEqual(af.validate_extraction(ok, self.docs), [])

    def test_document_outside_effective_period_or_not_official_is_rejected(self):
        old = self.ex([self.q("OLD", "AI_NOT_YET_REVENUE", "AI related products are still in development and have not generated revenue")],
                      checked=("OLD",), docs=("OLD", "R1", "R2", "CALL"))
        self.assertIn("DOC_OUTSIDE_EFFECTIVE_PERIOD:OLD", af.validate_extraction(old, self.docs))
        with self.assertRaisesRegex(ValueError, "NOT_OFFICIAL_DOC_TYPE"):
            af.register_doc(ISSUER, "NEWS", "https://example.test/n", b"x", "n.html", official_published_at=None)


class GradeC6(AiFieldsBase):
    def grade(self, ex): return af.grade_c6(ex, self.docs)

    def test_a_from_ai_numeric(self):
        g = self.grade(self.ex([self.q("R2", "AI_NUMERIC", "AI accelerator revenue was $55 million this quarter")]))
        self.assertEqual((g["grade"], g["value"]), ("A", "A"))

    def test_segment_proxy_needs_both_quotes(self):
        n = self.q("R2", "SEGMENT_NUMBER", "Segment revenue for data center products was $70 million")
        d = self.q("R2", "SEGMENT_AI_DRIVEN", "the increase was primarily driven by AI demand")
        both = self.grade(self.ex([n, d]))
        # v0.3.1：SEGMENT_PROXY 類 A 未複核前視為 B
        self.assertEqual((both["grade"], both["flags"]), ("B", ["SEGMENT_PROXY", "SEGMENT_PROXY_UNREVIEWED"]))
        self.assertEqual(both["reason"], "SEGMENT_PROXY_AWAITING_REVIEW")
        review = {"flag": af.REVIEW_FLAG_SEGMENT_PROXY, "issuer_id": ISSUER, "field": "C6",
                  "quote_sha256s": [af.quote_hash(n["quote"]), af.quote_hash(d["quote"])]}
        reviewed = af.grade_c6(self.ex([n, d]), self.docs, None, [review])
        self.assertEqual((reviewed["grade"], reviewed["flags"]), ("A", ["SEGMENT_PROXY", "SEGMENT_PROXY_REVIEWED"]))
        # 複核綁定特定引句：引句換掉就失效；別的發行人、別的旗標也不算
        other = self.q("R2", "SEGMENT_NUMBER", "Segment revenue for data center products was $70 million, and the increase")
        self.assertEqual(af.grade_c6(self.ex([other, d]), self.docs, None, [review])["grade"], "B")
        self.assertEqual(af.grade_c6(self.ex([n, d]), self.docs, None, [dict(review, issuer_id="ISSUER_OTHER")])["grade"], "B")
        self.assertEqual(af.grade_c6(self.ex([n, d]), self.docs, None, [dict(review, flag="OTHER")])["grade"], "B")
        # v0.3.2：只有部門數字、沒有部門 AI 句 → 不是 AI 營收歸因，不判 B；記入 ignored_mentions 後往下判（清單完成 → C）
        only = self.grade(self.ex([n]))
        self.assertEqual((only["grade"], only["flags"], only["reason"]), ("C", [], "CHECKLIST_COMPLETE_NO_AI_ATTRIBUTION"))
        self.assertEqual([m["reason"] for m in only["ignored_mentions"]], ["SEGMENT_NUMBER_WITHOUT_AI_LINK"])

    def test_b_when_ai_main_driver_without_number(self):
        g = self.grade(self.ex([self.q("R2", "AI_MAIN_DRIVER_NO_NUMBER", "Growth was driven mainly by AI demand across data center customers")]))
        self.assertEqual(g["grade"], "B")

    def test_has_driven_increased_demand_is_not_b(self):
        # v0.3：只說「帶動需求增加」沒有主要性字詞 → AI_MENTION_NO_PRIMACY，不能判級
        mention = self.q("R2", "AI_MAIN_DRIVER_NO_NUMBER", "Demand has driven increased demand for networking as AI deployment accelerates")
        self.assertEqual(af.effective_type(mention), "AI_MENTION_NO_PRIMACY")
        g = self.grade(self.ex([mention]))
        self.assertEqual(g["grade"], "C")                               # 沒有任何可判級的引句；清單完成 → C，而不是 B
        self.assertEqual(g["ignored_mentions"][0]["reason"], "AI_MENTION_NO_PRIMACY")
        loose = self.q("R2", "AI_MAIN_DRIVER_NO_NUMBER", "AI is discussed throughout this report")
        self.assertEqual(self.grade(self.ex([loose]))["grade"], "C")

    def test_primacy_word_must_be_in_the_same_sentence_as_ai(self):
        self.assertTrue(af.sentence_has_ai_and_primacy("Growth was driven mainly by AI demand."))
        self.assertFalse(af.sentence_has_ai_and_primacy("AI is discussed throughout this report. Our largest customers are hyperscalers."))
        self.assertFalse(af.sentence_has_ai_and_primacy("Revenue grew because of strong AI demand"))
        self.assertTrue(af.sentence_has_ai_and_primacy("AI 是我們營收成長的主要來源"))
        for word in ("primary", "primarily", "mainly", "largest", "majority", "most of", "driven primarily", "主要", "最大", "多數", "過半", "大部分"):
            self.assertTrue(af.PRIMACY_RE.search(f"AI is the {word} reason"), word)
        self.assertIsNone(af.PRIMACY_RE.search("maintain domain AI"))       # 不可把 maintain／domain 當成 main
        two = self.q("R2", "AI_MAIN_DRIVER_NO_NUMBER", "AI is discussed throughout this report. Our largest customers are hyperscalers.")
        self.assertEqual(af.effective_type(two), "AI_MENTION_NO_PRIMACY")

    def test_primacy_word_must_directly_modify_ai(self):
        # v0.3.1：MU 型句子——primarily 修飾出貨與售價，AI 在後面很遠 → 不算 SEGMENT_AI_DRIVEN
        mu_like = ("Segment revenue increased 120% primarily due to increases in bit shipments and average selling prices "
                   "driven by accelerating AI demand in cloud markets")
        n = self.q("R2", "SEGMENT_NUMBER", "Segment revenue for data center products was $70 million")
        driven = self.q("R2", "SEGMENT_AI_DRIVEN", mu_like)
        self.assertEqual(af.validate_extraction(self.ex([n, driven]), self.docs), [])
        self.assertEqual(af.effective_type(driven), "AI_MENTION_NO_PRIMACY")
        g = self.grade(self.ex([n, driven]))
        self.assertEqual(g["grade"], "C")                                   # v0.3.2：部門數字＋不合格 AI 句 → 不判 B
        self.assertEqual(sorted(m["reason"] for m in g["ignored_mentions"]), ["AI_MENTION_NO_PRIMACY", "SEGMENT_NUMBER_WITHOUT_AI_LINK"])
        self.assertFalse(af.sentence_has_ai_and_primacy(mu_like))
        for good in ("Growth was driven mainly by AI demand across data center customers", "AI is the main driver of our growth",
                     "Revenue was primarily driven by AI", "The majority of our growth came from AI customers.",
                     "AI 是我們營收成長的主要來源", "營收主要來自 AI 伺服器"):
            self.assertTrue(af.sentence_has_ai_and_primacy(good), good)
        for bad in ("We maintain domain expertise in AI", "AI is discussed. Our largest customers are hyperscalers."):
            self.assertFalse(af.sentence_has_ai_and_primacy(bad), bad)
        # 數字內的小數點不可把句子切開：43.09 之後的 primarily 不能和前面的 AI 湊成一句
        self.assertFalse(af.sentence_has_ai_and_primacy("AI revenue was $43.09 billion. Costs rose primarily because of freight"))

    def test_limitations_are_validated_and_carried_into_results(self):
        ok = self.ex(); ok["limitations"] = ["PRODUCT_MIX_IMAGE_NOT_OCR"]
        self.assertEqual(af.validate_extraction(ok, self.docs), [])
        g = self.grade(ok)
        self.assertEqual((g["grade"], g["limitations"]), ("C", ["PRODUCT_MIX_IMAGE_NOT_OCR"]))
        bad = self.ex(); bad["limitations"] = ["MADE_UP"]
        self.assertIn("LIMITATION_UNKNOWN:MADE_UP", af.validate_extraction(bad, self.docs))
        self.assertEqual(self.grade(self.ex())["limitations"], [])

    def test_segment_ai_driven_also_needs_primacy_in_same_sentence(self):
        n = self.q("R2", "SEGMENT_NUMBER", "Segment revenue for data center products was $70 million")
        weak = self.q("R2", "SEGMENT_AI_DRIVEN", "Demand has driven increased demand for networking as AI deployment accelerates")
        g = self.grade(self.ex([n, weak]))
        self.assertEqual(g["grade"], "C"); self.assertNotIn("SEGMENT_PROXY", g["flags"])

    def test_v032_segment_number_without_ai_link_falls_through(self):
        # v0.3.2：部門數字單獨出現不再是 B；B 只能來自 AI_MAIN_DRIVER_NO_NUMBER 或「部門數字＋部門 AI 句（未複核）」
        self.assertEqual(af.RUBRIC_C6, "C6_TABLE_V0.3.2")
        n = self.q("R2", "SEGMENT_NUMBER", "Segment revenue for data center products was $70 million")
        # 繼續往下判：有 D1 引句 → D1；有 AI_MAIN_DRIVER_NO_NUMBER → B（理由是 AI 主因句，不是部門數字）
        d1 = self.q("R2", "AI_NOT_YET_REVENUE", "Our AI products have not yet generated revenue and are in validation")
        self.assertEqual(self.grade(self.ex([n, d1]))["grade"], "D1")
        b = self.q("R2", "AI_MAIN_DRIVER_NO_NUMBER", "Growth was driven mainly by AI demand across data center customers")
        g = self.grade(self.ex([n, b]))
        self.assertEqual((g["grade"], g["reason"]), ("B", "AI_MAIN_DRIVER_NO_NUMBER"))
        # 清單未完成 → null，不因部門數字而判 B
        g = self.grade(self.ex([n], checked=("R1", "R2")))
        self.assertIsNone(g["grade"]); self.assertEqual(g["reason"], "CHECKLIST_INCOMPLETE")
        # 美股 MU 型：部門數字＋只有 AI_MENTION_NO_PRIMACY，沒有法說資料但有「自動化拿不到」紀錄 → C＋C_WITHOUT_CALL_MATERIALS
        docs = [d for d in self.docs if d["doc_id"] != "CALL"]
        mention = self.q("R2", "AI_MENTION_NO_PRIMACY", "Demand has driven increased demand for networking as AI deployment accelerates")
        ex = self.ex([n, mention], checked=("R1", "R2"), docs=("R1", "R2"))
        ex["call_materials_unavailable"] = {"code": af.CALL_UNAVAILABLE_CODE, "attempts": [{"url": "https://ir.example.test/", "error": "HTTP 403"}]}
        g = af.grade_c6(ex, docs, "US")
        self.assertEqual((g["grade"], g["flags"]), ("C", ["C_WITHOUT_CALL_MATERIALS"]))

    def test_incomplete_checklist_is_null_not_c(self):
        g = self.grade(self.ex(checked=("R1", "R2")))                      # CALL 未勾
        self.assertIsNone(g["grade"]); self.assertEqual(g["reason"], "CHECKLIST_INCOMPLETE"); self.assertEqual(g["missing_doc_ids"], ["CALL"])
        g = self.grade(self.ex(checked=()))
        self.assertIsNone(g["value"])

    def test_complete_checklist_and_no_attribution_is_c(self):
        g = self.grade(self.ex())
        self.assertEqual((g["grade"], g["value"]), ("C", "C"))

    # ---- G-S4-C6q／C6r／C6s（2026-10-18）
    def test_c6q_mixed_category_number_cannot_be_ai_numeric(self):
        for text in ("應用於AI 基礎設施及伺服器的營收占比超過20%", "雲端與 AI 營收占比約 30%", "Data center and AI revenue was $55 million this quarter",
                     "AI and cloud revenue was $55 million this quarter"):
            self.assertTrue(af.AI_MIXED_CATEGORY_RE.search(af.normalize(text)), text)
        # 只有 AI 本身：下界寫法也算數字
        self.assertFalse(af.AI_MIXED_CATEGORY_RE.search("AI 營收占比超過20%"))
        self.assertFalse(af.AI_MIXED_CATEGORY_RE.search("Q3 AI semiconductor revenue of $16.7 billion grew 221% year-over-year, and 54% quarter-over-quarter"))
        self.assertTrue(af.validate_extraction(self.ex([self.q("R2", "AI_NUMERIC", "AI accelerator revenue was $55 million this quarter")]), self.docs) == [])

    def test_c6q_mixed_category_declared_as_segment_number_is_not_a(self):
        # 混合類別的句子記為 SEGMENT_NUMBER（沒有部門 AI 句）→ 不判 A，也不判 B，往下判 C
        g = af.grade_c6(self.ex([self.q("R2", "SEGMENT_NUMBER", "Segment revenue for data center products was $70 million")]), self.docs, "US")
        self.assertNotIn(g["grade"], ("A", "B"))

    def test_c6r_chinese_primacy_wording(self):
        for text in ("2026年第二季營收季增78%,年增超過400%,主係受惠於AI強勁需求", "營收成長主因AI需求強勁", "營收成長主要係受惠於AI需求", "營收成長主要受惠於AI需求"):
            self.assertTrue(af.sentence_has_ai_and_primacy(text), text)
        for text in ("營收成長受惠於AI需求", "主題為AI應用的專案說明會與其他很長的說明文字讓兩者距離超過十個字元之遙"):
            self.assertFalse(af.sentence_has_ai_and_primacy(text), text)
        ex = self.ex([self.q("R2", "SEGMENT_NUMBER", "Segment revenue for data center products was $70 million"),
                      self.q("R2", "SEGMENT_AI_DRIVEN", "Growth was driven mainly by AI demand across data center customers")])
        self.assertEqual(af.grade_c6(ex, self.docs)["grade"], "B")

    def test_c6s_taiwan_none_listed_is_c_without_call_materials(self):
        docs = [d for d in self.docs if d["doc_id"] != "CALL"]
        ex = self.ex(checked=("R1", "R2"), docs=("R1", "R2"))
        g = af.grade_c6(ex, docs, "TW")
        self.assertEqual((g["grade"], g["reason"]), (None, "TW_CALL_MATERIALS_REQUIRED"))
        ex["call_materials_none_listed"] = {"code": af.CALL_NONE_LISTED_CODE, "queried_at": "2026-10-18T22:44:32+08:00",
                                            "query": "ajax_t100sb02_1 TYPEK=sii year=115 month=（全年） co_id=TEST；115/07/01 之後無場次"}
        self.assertEqual(af.validate_extraction(ex, docs), [])
        g = af.grade_c6(ex, docs, "TW")
        self.assertEqual((g["grade"], g["flags"]), ("C", ["C_WITHOUT_CALL_MATERIALS"]))
        for bad in ({"code": af.CALL_NONE_LISTED_CODE, "queried_at": "2026-10-18T22:44:32+08:00"},       # 缺查詢條件
                    {"code": af.CALL_NONE_LISTED_CODE, "query": "q"},                                      # 缺時間
                    {"code": af.CALL_NONE_LISTED_CODE, "query": "q", "queried_at": "不是時間"},
                    {"code": "OTHER", "query": "q", "queried_at": "2026-10-18T22:44:32+08:00"}):
            self.assertIn("CALL_NONE_LISTED_ATTESTATION_INVALID", af.validate_extraction(dict(ex, call_materials_none_listed=bad), docs))
        # 清單未完成仍是 null；美股不能用這個紀錄；台股「自動化拿不到」紀錄仍不適用
        self.assertEqual(af.grade_c6(dict(ex, checklist=[{"doc_id": "R1", "checked": True}, {"doc_id": "R2", "checked": False}]), docs, "TW")["reason"],
                         "CHECKLIST_INCOMPLETE")
        self.assertEqual(af.grade_c6(ex, docs, "US")["reason"], "CALL_MATERIALS_MISSING")

    # ---- G-S4-C6t／T30g
    def test_c6t_segment_number_must_not_be_growth_only(self):
        for text in ("CMS事業 ❑ 2026年第二季營收季增78%,年增超過400%", "Segment revenue grew 40% y/y and 12% q/q", "資料中心營收年增 65%，季增 12%"):
            self.assertTrue(af.segment_number_is_growth_only(text), text)
        for text in ("客製化記憶體產品 $ 49,583,914 快閃記憶體產品 32,038,563", "Segment revenue for data center products was $70 million",
                     "Q2 record data center revenue of $2,172M increased 46% y/y and 18% q/q",
                     "Revenues for the year ended June 30, 2026 for Datacenter & Communications increased 40% to $5,275 million",
                     "部門營收占比約 51%", "Server 32.4 66% 15.0"):
            self.assertFalse(af.segment_number_is_growth_only(text), text)
        growth = self.ex([self.q("R2", "SEGMENT_NUMBER", "Segment revenue for data center grew 120% y/y and 30% q/q")])
        self.assertIn("SEGMENT_NUMBER_GROWTH_ONLY:0", af.validate_extraction(growth, self.docs))
        ok = self.ex([self.q("R2", "SEGMENT_NUMBER", "Segment revenue for data center products was $70 million")])
        self.assertEqual(af.validate_extraction(ok, self.docs), [])
        self.assertIsNone(af.grade_c6(growth, self.docs)["grade"])      # 驗證失敗 → 不判級

    def test_segment_proxy_review_failed_is_still_b_and_binds_the_quote_pair(self):
        n = self.q("R2", "SEGMENT_NUMBER", "Segment revenue for data center products was $70 million")
        d = self.q("R2", "SEGMENT_AI_DRIVEN", "Growth was driven mainly by AI demand across data center customers")
        ex = self.ex([n, d])
        pair = [af.quote_hash(n["quote"]), af.quote_hash(d["quote"])]
        if not project_lock.read(): project_lock.acquire("t", "x")
        with self.assertRaisesRegex(ValueError, "REVIEW_FAIL_REASON_UNKNOWN"):
            af.append_review(af.REVIEW_FLAG_SEGMENT_PROXY_FAILED, ISSUER, "C6", "2026-06-30", pair, "human", "n", lock_holder="t")
        with self.assertRaisesRegex(ValueError, "REVIEW_REASON_ONLY_FOR_FAILED"):
            af.append_review(af.REVIEW_FLAG_SEGMENT_PROXY, ISSUER, "C6", "2026-06-30", pair, "human", "n", lock_holder="t", reason_code="GROWTH_ATTRIBUTION_ONLY")
        r = af.append_review(af.REVIEW_FLAG_SEGMENT_PROXY_FAILED, ISSUER, "C6", "2026-06-30", pair, "human", "只說成長", lock_holder="t",
                             reason_code="GROWTH_ATTRIBUTION_ONLY")
        self.assertEqual(r["reason_code"], "GROWTH_ATTRIBUTION_ONLY")
        g = af.grade_c6(ex, self.docs, None, [r])
        self.assertEqual((g["grade"], g["reason"]), ("B", "SEGMENT_PROXY_REVIEW_FAILED")); self.assertIn("SEGMENT_PROXY_REVIEW_FAILED", g["flags"])
        self.assertEqual(af.grade_c6(ex, self.docs, None, [dict(r, quote_sha256s=["x"])])["reason"], "SEGMENT_PROXY_AWAITING_REVIEW")   # 引句改變即失效
        self.assertEqual(af.grade_c6(ex, self.docs, None, [dict(r, issuer_id="ISSUER_OTHER")])["reason"], "SEGMENT_PROXY_AWAITING_REVIEW")

    def test_call_materials_rules_by_market(self):
        docs = [d for d in self.docs if d["doc_id"] != "CALL"]
        ex = self.ex(checked=("R1", "R2"), docs=("R1", "R2"))
        # 美股：沒有法說資料也沒有「自動化拿不到」紀錄 → null
        g = af.grade_c6(ex, docs, "US")
        self.assertIsNone(g["grade"]); self.assertEqual(g["reason"], "CALL_MATERIALS_MISSING")
        # 美股：有 CALL_MATERIALS_UNAVAILABLE_AUTOMATED（附網址與錯誤）→ 可判 C，標 C_WITHOUT_CALL_MATERIALS
        ex["call_materials_unavailable"] = {"code": af.CALL_UNAVAILABLE_CODE, "attempts": [{"url": "https://ir.example.test/", "error": "HTTP 403"}]}
        g = af.grade_c6(ex, docs, "US")
        self.assertEqual((g["grade"], g["flags"]), ("C", ["C_WITHOUT_CALL_MATERIALS"]))
        # 台股：必須有 MOPS 法人說明會簡報；自動化拿不到的紀錄不適用
        g = af.grade_c6(ex, docs, "TW")
        self.assertIsNone(g["grade"]); self.assertEqual(g["reason"], "TW_CALL_MATERIALS_REQUIRED")
        # 台股有 MOPS 法說會簡報 → 正常判 C，不標旗標
        g = af.grade_c6(self.ex(), [dict(d, doc_type="MOPS-INVESTOR-CONFERENCE") if d["doc_id"] == "CALL" else d for d in self.docs], "TW")
        self.assertEqual((g["grade"], g["flags"]), ("C", []))
        # 嘗試紀錄不完整 → 驗證錯誤
        bad = self.ex(checked=("R1", "R2"), docs=("R1", "R2"))
        bad["call_materials_unavailable"] = {"code": af.CALL_UNAVAILABLE_CODE, "attempts": [{"url": "https://ir.example.test/"}]}
        self.assertIn("CALL_UNAVAILABLE_ATTESTATION_INVALID", af.validate_extraction(bad, docs))
        bad["call_materials_unavailable"] = {"code": af.CALL_UNAVAILABLE_CODE, "attempts": []}
        self.assertIn("CALL_UNAVAILABLE_ATTESTATION_INVALID", af.validate_extraction(bad, docs))

    def test_us_c_without_call_materials_still_needs_complete_checklist(self):
        docs = [d for d in self.docs if d["doc_id"] != "CALL"]
        ex = self.ex(checked=("R1",), docs=("R1", "R2"))
        ex["call_materials_unavailable"] = {"code": af.CALL_UNAVAILABLE_CODE, "attempts": [{"url": "https://ir.example.test/", "error": "HTTP 403"}]}
        g = af.grade_c6(ex, docs, "US")
        self.assertIsNone(g["grade"]); self.assertEqual(g["reason"], "CHECKLIST_INCOMPLETE")

    def test_d_requires_latest_report_period_quote_and_precedes_c(self):
        d1 = self.q("R2", "AI_NOT_YET_REVENUE", "Our AI products have not yet generated revenue and are in validation")
        g = self.grade(self.ex([d1]))
        self.assertEqual((g["grade"], g["value"]), ("D1", "D"))
        # 同一份抽取在清單完成、可以判 C 的情況下，D 仍優先（判定順序 A → B → D → C）
        self.assertEqual(self.grade(self.ex())["grade"], "C")
        self.assertEqual(self.grade(self.ex([d1]))["grade"], "D1")
        d2 = self.q("R2", "AI_NEGLIGIBLE", "Our AI products have not yet generated revenue")
        self.assertEqual(self.grade(self.ex([d2]))["grade"], "D2")
        b = self.q("R2", "AI_MAIN_DRIVER_NO_NUMBER", "Growth was driven mainly by AI demand across data center customers")
        self.assertEqual(self.grade(self.ex([d1, b]))["grade"], "B")        # B 仍優先於 D
        # 期間內但早於最新財報發布日的文件（8-K，6/15）：D 引句不算，須最新一份財報期間內的引句
        TEXTS["MID"] = "Interim update on June 15, 2026. Our AI products have not yet generated revenue and are in validation."
        mid = make_doc("MID", "8-K", "2026-06-15T00:00:00+00:00", folder=af.RAW_DOCS, key="MID")
        docs = self.docs + [mid]
        quote = self.q("MID", "AI_NOT_YET_REVENUE", "Our AI products have not yet generated revenue and are in validation")
        ex = self.ex([quote], checked=("R1", "R2", "CALL", "MID"), docs=("R1", "R2", "CALL", "MID"))
        g = af.grade_c6(ex, docs)
        self.assertEqual(g["grade"], "C")          # D 不成立；清單完成且無 AI 歸因 → C
        del TEXTS["MID"]

    def test_c6_score_map_matches_rules(self):
        with open(os.path.join(self.old_root, "00_規範", "rules.json"), encoding="utf-8") as f: rules = json.load(f)
        self.assertEqual(rules["company"]["indicators"]["C6"]["rule"]["map"], {"A": 1, "B": 0, "C": 0, "D": -1})
        self.assertEqual({k: af.GRADE_SCORE[k] for k in ("A", "B", "C")}, {"A": 1, "B": 0, "C": 0})
        self.assertEqual({af.GRADE_SCORE["D1"], af.GRADE_SCORE["D2"]}, {-1})


class DualExtraction(AiFieldsBase):
    A_Q = "AI accelerator revenue was $55 million this quarter"
    B_Q = "Growth was driven mainly by AI demand across data center customers"

    def append(self, ex, prompt="prompt"):
        if not project_lock.read(): project_lock.acquire("t", "x")
        return af.append_extraction(ex, prompt, self.docs, lock_holder="t")

    def test_agreement_is_verified(self):
        e1 = self.append(self.ex([self.q("R2", "AI_NUMERIC", self.A_Q)], extractor="A"))
        e2 = self.append(self.ex([self.q("R2", "AI_NUMERIC", self.A_Q)], extractor="B"))
        r = af.compare_dual(e1, e2, self.docs)
        self.assertEqual((r["value"], r["status"]), ("A", af.STATUS_VERIFIED))

    def test_disagreement_takes_lower_grade_and_flags(self):
        e1 = self.append(self.ex([self.q("R2", "AI_NUMERIC", self.A_Q)], extractor="A"))
        e2 = self.append(self.ex([self.q("R2", "AI_MAIN_DRIVER_NO_NUMBER", self.B_Q)], extractor="B"))
        r = af.compare_dual(e1, e2, self.docs)
        self.assertEqual((r["value"], r["status"]), ("B", af.STATUS_DISAGREE))
        self.assertIn("REVIEW_DISAGREEMENT", r["detail"]["flags"])
        # B 與 C 同分：取證據較少者 C
        e3 = self.append(self.ex(extractor="C"))
        r = af.compare_dual(e2, e3, self.docs)
        self.assertEqual((r["value"], r["status"]), ("C", af.STATUS_DISAGREE))

    def test_one_side_null_makes_result_null(self):
        e1 = self.append(self.ex([self.q("R2", "AI_NUMERIC", self.A_Q)], extractor="A"))
        e2 = self.append(self.ex(checked=("R1", "R2"), extractor="B"))             # 清單未完成 → null
        r = af.compare_dual(e1, e2, self.docs)
        self.assertIsNone(r["value"]); self.assertEqual(r["status"], af.STATUS_INSUFFICIENT)

    def test_v031_extractions_are_regraded_under_v032(self):
        # v0.3.2 只改判級：v0.3.1 抽取不必重抽，可與 v0.3.2 抽取比對；更舊的版本（v0.2）仍拒絕
        n = self.q("R2", "SEGMENT_NUMBER", "Segment revenue for data center products was $70 million")
        e1 = dict(self.append(self.ex([n], extractor="A")), rubric_version="C6_TABLE_V0.3.1")
        e2 = self.append(self.ex([n], extractor="B"))
        self.assertEqual(e2["rubric_version"], "C6_TABLE_V0.3.2")
        r = af.compare_dual(e1, e2, self.docs)
        self.assertEqual((r["value"], r["status"]), ("C", af.STATUS_VERIFIED))
        old = af.compare_dual(dict(e1, rubric_version="C6_TABLE_V0.2"), e2, self.docs)
        self.assertIsNone(old["value"]); self.assertIn("RUBRIC_VERSION_MISMATCH", old["problems"])

    def test_same_extractor_cannot_verify_itself(self):
        e1 = self.append(self.ex([self.q("R2", "AI_NUMERIC", self.A_Q)], extractor="A"))
        r = af.compare_dual(e1, copy.deepcopy(e1), self.docs)
        self.assertIsNone(r["value"]); self.assertIn("SAME_EXTRACTOR", r["problems"])
        dependent = dict(self.append(self.ex([self.q("R2", "AI_NUMERIC", self.A_Q)], extractor="B")), derived_from_extraction_id=e1["extraction_id"])
        self.assertIn("NOT_INDEPENDENT", af.compare_dual(e1, dependent, self.docs)["problems"])

    def test_ledgers_are_append_only_lock_gated_and_replayable(self):
        project_lock.release("t") if project_lock.read() else None
        with self.assertRaises(PermissionError):
            af.append_extraction(self.ex([self.q("R2", "AI_NUMERIC", self.A_Q)]), "p", self.docs)
        e1 = self.append(self.ex([self.q("R2", "AI_NUMERIC", self.A_Q)], extractor="A"))
        with self.assertRaisesRegex(ValueError, "DUPLICATE_ID"):
            af.append_extraction(
                self.ex([self.q("R2", "AI_NUMERIC", self.A_Q)], extractor="A"),
                "prompt",
                self.docs,
                lock_holder="t",
            )
        e2 = self.append(self.ex([self.q("R2", "AI_NUMERIC", self.A_Q)], extractor="B"))
        row = af.append_field_value(e1, e2, self.docs, lock_holder="t")
        self.assertEqual((row["value"], row["status"]), ("A", af.STATUS_VERIFIED))
        self.assertEqual(row["evidence_kind"], "RETROSPECTIVE_EXTRACTION")
        self.assertTrue(all(af.ledger_row_ok(r) for r in af.read_jsonl(af.EXTRACTIONS) + af.read_jsonl(af.FIELD_VALUES)))
        again = af.recompute_value(row, af.read_jsonl(af.EXTRACTIONS), self.docs)
        self.assertEqual((again["value"], again["status"]), (row["value"], row["status"]))
        # 抽取列被改動 → 雜湊檢查失敗
        tampered = dict(af.read_jsonl(af.EXTRACTIONS)[0], quotes=[])
        self.assertFalse(af.ledger_row_ok(tampered))

    def test_review_ledger_binds_to_quote_hashes_and_changes_value_id(self):
        n = self.q("R2", "SEGMENT_NUMBER", "Segment revenue for data center products was $70 million")
        d = self.q("R2", "SEGMENT_AI_DRIVEN", "the increase was primarily driven by AI demand")
        e1 = self.append(self.ex([n, d], extractor="A")); e2 = self.append(self.ex([n, d], extractor="B"))
        first = af.append_field_value(e1, e2, self.docs, lock_holder="t")
        self.assertEqual((first["value"], first["status"]), ("B", af.STATUS_VERIFIED))     # 未複核 → B
        self.assertIn("SEGMENT_PROXY_UNREVIEWED", first["grade_detail"]["flags"])
        with self.assertRaisesRegex(ValueError, "REVIEW_NEEDS_REVIEWER_AND_NOTE"):
            af.append_review(af.REVIEW_FLAG_SEGMENT_PROXY, ISSUER, "C6", "2026-06-30", [af.quote_hash(n["quote"])], "", "")
        with self.assertRaisesRegex(ValueError, "REVIEW_FLAG_UNKNOWN"):
            af.append_review("OTHER", ISSUER, "C6", "2026-06-30", [], "r", "n")
        review = af.append_review(af.REVIEW_FLAG_SEGMENT_PROXY, ISSUER, "C6", "2026-06-30",
                                  [af.quote_hash(n["quote"]), af.quote_hash(d["quote"])], "human", "部門句確實以 AI 為主因",
                                  lock_holder="t")
        second = af.append_field_value(e1, e2, self.docs, lock_holder="t")
        self.assertEqual((second["value"], second["status"]), ("A", af.STATUS_VERIFIED))
        self.assertIn("SEGMENT_PROXY_REVIEWED", second["grade_detail"]["flags"])
        self.assertNotEqual(first["value_id"], second["value_id"]); self.assertEqual(second["review_ids"], [review["review_id"]])
        again = af.recompute_value(second, af.read_jsonl(af.EXTRACTIONS), self.docs)
        self.assertEqual(again["value"], "A")
        self.assertEqual(af.recompute_value(first, af.read_jsonl(af.EXTRACTIONS), self.docs)["value"], "B")

    def test_value_stops_being_current_when_a_newer_report_arrives(self):
        e1 = self.append(self.ex([self.q("R2", "AI_NUMERIC", self.A_Q)], extractor="A"))
        e2 = self.append(self.ex([self.q("R2", "AI_NUMERIC", self.A_Q)], extractor="B"))
        row = af.append_field_value(e1, e2, self.docs, lock_holder="t")
        self.assertTrue(af.is_current(row, self.docs))
        TEXTS["R3"] = "Quarterly report for the quarter ended September 30, 2026."
        newer = self.docs + [make_doc("R3", "10-Q", "2026-11-01T00:00:00+00:00", "2026-09-30", folder=af.RAW_DOCS, key="R3")]
        self.assertFalse(af.is_current(row, newer))      # 有效期間移動 → 舊值失效，須重抽
        del TEXTS["R3"]


class C5Computation(AiFieldsBase):
    ACQ = "Revenue from the acquired Beta business was $30 million in the quarter"
    ONE = "One-time license revenue of $25 million was recognized in the quarter"
    NONE_Q = "There were no other material acquisitions during the period"
    ALL_SECTIONS = ["BUSINESS_COMBINATIONS_NOTE", "REVENUE_NOTE", "MDNA_OR_OPERATIONS"]

    def ex5(self, quotes=(), facts=(), sections=None, checked=True, extractor="A"):
        sections = self.ALL_SECTIONS if sections is None else sections
        return {"field": "C5", "issuer_id": ISSUER, "target_period": "2026-06-30", "doc_ids": ["R1", "R2", "CALL"],
                "checklist": [{"doc_id": "R2", "checked": checked, "sections": list(sections)},
                              {"doc_id": "R1", "checked": True, "sections": []}, {"doc_id": "CALL", "checked": True, "sections": []}],
                "quotes": list(quotes), "facts": list(facts), "extractor_id": extractor}

    def acq(self, value=30, unit="USD million", qi=0):
        return {"name": "acquired_revenue", "value": value, "unit": unit, "quote_index": qi}

    def one(self, value=25, unit="USD million", qi=1):
        return {"name": "one_time_revenue", "value": value, "unit": unit, "quote_index": qi}

    @staticmethod
    def rev(current, prior, currency="USD", period="2026-06-30"):
        return {"period_end": period, "prior_period_end": "2025-06-30", "current": current, "prior": prior,
                "currency": currency, "observation_ids": ["OBS_CUR", "OBS_PRIOR"]}

    def both(self):
        return [self.q("R2", "ACQUIRED_REVENUE", self.ACQ), self.q("R2", "ONE_TIME_REVENUE", self.ONE)]

    # ---- 有
    def test_yes_when_acquired_plus_one_time_is_at_least_half_of_growth(self):
        g = af.grade_c5(self.ex5(self.both(), [self.acq(), self.one()]), self.docs, self.rev(200e6, 100e6))
        self.assertEqual(g["value"], "有")                                   # X＝55M ≥ 0.5 × ΔRev(100M)
        self.assertEqual((g["calc"]["delta_rev"], g["calc"]["x"], g["calc"]["threshold"]), (100e6, 55e6, 50e6))
        self.assertEqual({e["fact"] for e in g["evidence"]}, {"acquired_revenue", "one_time_revenue"})
        self.assertEqual(g["calc"]["revenue_observation_ids"], ["OBS_CUR", "OBS_PRIOR"])

    def test_exactly_half_counts_as_yes(self):
        g = af.grade_c5(self.ex5(self.both(), [self.acq(), self.one()]), self.docs, self.rev(210e6, 100e6))
        self.assertEqual(g["value"], "有")                                   # 55M ≥ 0.5 × 110M＝55M（等號也算過半）

    def test_decline_masked_by_acquisition_is_yes(self):
        q = [self.q("R2", "ACQUIRED_REVENUE", self.ACQ)]
        g = af.grade_c5(self.ex5(q, [self.acq()]), self.docs, self.rev(990e6, 1000e6))
        self.assertEqual(g["value"], "有")                                   # ΔRev≤0 且 30M ≥ 2% × 1000M＝20M
        self.assertEqual(g["calc"]["threshold"], 20e6)
        g = af.grade_c5(self.ex5(q, [self.acq()]), self.docs, self.rev(1990e6, 2000e6))
        self.assertEqual(g["value"], "無")                                   # 30M < 2% × 2000M＝40M

    # ---- 無
    def test_no_when_disclosed_amount_is_below_half(self):
        q = [self.q("R2", "ACQUIRED_REVENUE", self.ACQ)]
        g = af.grade_c5(self.ex5(q, [self.acq()]), self.docs, self.rev(200e6, 100e6))
        self.assertEqual((g["value"], g["calc"]["x"]), ("無", 30e6))

    def test_no_disclosure_with_complete_checklist_counts_x_as_zero(self):
        g = af.grade_c5(self.ex5([self.q("R2", "NO_MATERIAL_ACQUISITION", self.NONE_Q)]), self.docs, self.rev(200e6, 100e6))
        self.assertEqual((g["value"], g["calc"]["x"]), ("無", 0.0)); self.assertIn("X_ZERO_NO_DISCLOSURE", g["flags"])
        self.assertEqual(g["evidence"][0]["fact"], "no_material_acquisition")

    # ---- null
    def test_no_disclosure_with_incomplete_checklist_is_null(self):
        for ex in (self.ex5(sections=["REVENUE_NOTE"]), self.ex5(checked=False)):
            g = af.grade_c5(ex, self.docs, self.rev(200e6, 100e6))
            self.assertIsNone(g["value"]); self.assertEqual(g["reason"], "CHECKLIST_INCOMPLETE")
        g = af.grade_c5(self.ex5(sections=["REVENUE_NOTE"]), self.docs, self.rev(200e6, 100e6))
        self.assertEqual(g["calc"]["missing_sections"], ["BUSINESS_COMBINATIONS_NOTE", "MDNA_OR_OPERATIONS"])

    def test_incomplete_checklist_below_threshold_is_null_but_lower_bound_above_is_yes(self):
        q = [self.q("R2", "ACQUIRED_REVENUE", self.ACQ)]
        g = af.grade_c5(self.ex5(q, [self.acq()], sections=["REVENUE_NOTE"]), self.docs, self.rev(200e6, 100e6))
        self.assertIsNone(g["value"])                                          # 30M < 50M 且還有附註沒查 → 不能判無
        g = af.grade_c5(self.ex5(self.both(), [self.acq(), self.one()], sections=["REVENUE_NOTE"]), self.docs, self.rev(200e6, 100e6))
        self.assertEqual((g["value"], g["flags"]), ("有", ["X_LOWER_BOUND_CHECKLIST_INCOMPLETE"]))

    def test_latest_filing_must_be_in_ledger_for_the_target_period(self):
        ex = self.ex5(); ex["checklist"][0]["doc_id"] = "R1"; ex["checklist"][1]["doc_id"] = "R2"   # 只查了舊一季
        ex["checklist"][0]["sections"], ex["checklist"][1]["sections"] = self.ALL_SECTIONS, []
        self.assertIsNone(af.grade_c5(ex, self.docs, self.rev(200e6, 100e6))["value"])
        ex = self.ex5(); ex["target_period"] = "2026-09-30"
        g = af.grade_c5(ex, self.docs, self.rev(200e6, 100e6, period="2026-09-30"))
        self.assertIsNone(g["value"]); self.assertEqual(g["reason"], "LATEST_FILING_NOT_IN_LEDGER")

    def test_revenue_must_come_from_c1_for_the_same_period_and_currency(self):
        ex = self.ex5([self.q("R2", "ACQUIRED_REVENUE", self.ACQ)], [self.acq()])
        self.assertEqual(af.grade_c5(ex, self.docs, None)["reason"], "C1_REVENUE_UNAVAILABLE")
        self.assertEqual(af.grade_c5(ex, self.docs, self.rev(200e6, 100e6, period="2026-03-31"))["reason"], "REVENUE_PERIOD_MISMATCH")
        self.assertEqual(af.grade_c5(ex, self.docs, self.rev(200e6, 100e6, currency="TWD"))["reason"], "CURRENCY_MISMATCH")
        self.assertEqual(af.grade_c5(ex, self.docs, self.rev(200e6, 0))["reason"], "PRIOR_REVENUE_NOT_POSITIVE")

    def test_units_are_scaled_before_comparing(self):
        q = [self.q("R2", "ACQUIRED_REVENUE", "本季併購之子公司貢獻營收新台幣 3,000 萬元")]
        fact = {"name": "acquired_revenue", "value": 30000, "unit": "TWD thousand", "quote_index": 0}
        self.assertIn("FACT_VALUE_NOT_IN_QUOTE:0", af.validate_extraction(self.ex5(q, [fact]), self.docs))   # 30000 不在引句（引句是 3,000 萬）
        fact = {"name": "acquired_revenue", "value": 3000, "unit": "TWD", "quote_index": 0}                  # 單位寫錯（3,000 元）
        g = af.grade_c5(self.ex5(q, [fact]), self.docs, self.rev(200e6, 100e6, currency="TWD"))
        self.assertEqual((g["value"], g["calc"]["x"]), ("無", 3000.0))
        self.assertEqual(af.parse_money_unit("USD million"), ("USD", 1e6)); self.assertEqual(af.parse_money_unit("twd thousand"), ("TWD", 1e3))
        self.assertIsNone(af.parse_money_unit("EUR")); self.assertIsNone(af.parse_money_unit("USD lakh"))

    # ---- 驗證
    def test_c5_extraction_validation(self):
        good = self.ex5(self.both(), [self.acq(), self.one()])
        self.assertEqual(af.validate_extraction(good, self.docs), [])
        tampered = self.ex5([self.q("R2", "ACQUIRED_REVENUE", self.ACQ.replace("$30", "$80"))], [self.acq(80)])
        self.assertIn("QUOTE_NOT_IN_SOURCE:0", af.validate_extraction(tampered, self.docs))
        cases = {
            "C5_FACT_NAME_INVALID:0:organic_revenue": self.ex5([self.q("R2", "ACQUIRED_REVENUE", self.ACQ)], [dict(self.acq(), name="organic_revenue")]),
            "C5_FACT_DUPLICATE:acquired_revenue": self.ex5([self.q("R2", "ACQUIRED_REVENUE", self.ACQ)], [self.acq(), self.acq()]),
            "C5_FACT_UNIT_INVALID:0:EUR": self.ex5([self.q("R2", "ACQUIRED_REVENUE", self.ACQ)], [self.acq(unit="EUR")]),
            "C5_FACT_QUOTE_TYPE_MISMATCH:0": self.ex5([self.q("R2", "ONE_TIME_REVENUE", self.ACQ)], [self.acq()]),
            "C5_FACT_VALUE_INVALID:0": self.ex5([self.q("R2", "ACQUIRED_REVENUE", self.ACQ)], [self.acq(value=-30)]),
            "CHECKLIST_SECTION_INVALID:R2": self.ex5(sections=["SOMETHING_ELSE"]),
            "QUOTE_TYPE_INVALID:0:AI_NUMERIC": self.ex5([self.q("R2", "AI_NUMERIC", "AI accelerator revenue was $55 million this quarter")]),
        }
        for expected, ex in cases.items():
            self.assertIn(expected, af.validate_extraction(ex, self.docs), expected)
        self.assertIsNone(af.grade_c5(cases["C5_FACT_DUPLICATE:acquired_revenue"], self.docs, self.rev(200e6, 100e6))["value"])

    def test_c6_rules_do_not_apply_to_c5_and_vice_versa(self):
        self.assertEqual(af.validate_extraction(self.ex5([self.q("R2", "NO_MATERIAL_ACQUISITION", self.NONE_Q)]), self.docs), [])  # 無 AI 字樣也可
        c6 = self.ex([self.q("R2", "ACQUIRED_REVENUE", self.ACQ)])
        self.assertIn("QUOTE_TYPE_INVALID:0:ACQUIRED_REVENUE", af.validate_extraction(c6, self.docs))
        self.assertEqual(af.grade_c5(self.ex(), self.docs, self.rev(200e6, 100e6))["reason"], "NOT_C5")

    # ---- G-S4-2a：併購貢獻只揭露百分比
    PCT_Q = "Acquisitions of businesses 7 % Total increase in Net sales 21 %"

    def pct(self, value=7, unit="%", qi=0):
        return {"name": "acquired_revenue_pct_of_prior_year", "value": value, "unit": unit, "quote_index": qi}

    def test_percent_of_prior_year_is_converted_with_c1_prior_revenue(self):
        q = [self.q("R2", "ACQUIRED_REVENUE", self.PCT_Q)]
        g = af.grade_c5(self.ex5(q, [self.pct()]), self.docs, self.rev(1210e6, 1000e6))
        self.assertAlmostEqual(g["calc"]["acquired_revenue"], 70e6)       # 7% × 去年同季 1,000M
        self.assertEqual(g["calc"]["acquired_revenue_pct_of_prior_year"], 7.0)
        self.assertEqual(g["value"], "無")                                  # 70M < 0.5 × 210M
        g = af.grade_c5(self.ex5(q, [self.pct()]), self.docs, self.rev(1100e6, 1000e6))
        self.assertEqual(g["value"], "有")                                  # 70M ≥ 0.5 × 100M
        g = af.grade_c5(self.ex5(q, [self.pct()]), self.docs, self.rev(990e6, 1000e6))
        self.assertEqual(g["value"], "有")                                  # 衰退：70M ≥ 2% × 1,000M
        self.assertEqual(g["evidence"][0]["unit"], "%")

    def test_percent_fact_validation(self):
        q = [self.q("R2", "ACQUIRED_REVENUE", self.PCT_Q)]
        self.assertEqual(af.validate_extraction(self.ex5(q, [self.pct()]), self.docs), [])
        self.assertIn("C5_FACT_UNIT_INVALID:0:USD million", af.validate_extraction(self.ex5(q, [self.pct(unit="USD million")]), self.docs))
        self.assertIn("FACT_VALUE_NOT_IN_QUOTE:0", af.validate_extraction(self.ex5(q, [self.pct(value=9)]), self.docs))
        both = [self.q("R2", "ACQUIRED_REVENUE", self.PCT_Q), self.q("R2", "ACQUIRED_REVENUE", self.ACQ)]
        errs = af.validate_extraction(self.ex5(both, [self.pct(), self.acq(qi=1)]), self.docs)
        self.assertIn("C5_FACT_CONFLICT:acquired_revenue+acquired_revenue_pct_of_prior_year", errs)
        wrong_type = [self.q("R2", "ONE_TIME_REVENUE", self.PCT_Q)]
        self.assertIn("C5_FACT_QUOTE_TYPE_MISMATCH:0", af.validate_extraction(self.ex5(wrong_type, [self.pct()]), self.docs))

    # ---- G-S4-2b：台股兩處、美股三處
    def test_taiwan_checklist_needs_two_sections_us_needs_three(self):
        two = ["BUSINESS_COMBINATIONS_NOTE", "REVENUE_NOTE"]
        tw = af.grade_c5(self.ex5(sections=two), self.docs, self.rev(200e6, 100e6, currency="TWD"), market="TW")
        self.assertEqual((tw["value"], tw["calc"]["required_sections"]), ("無", two))
        us = af.grade_c5(self.ex5(sections=two), self.docs, self.rev(200e6, 100e6), market="US")
        self.assertIsNone(us["value"]); self.assertEqual(us["calc"]["missing_sections"], ["MDNA_OR_OPERATIONS"])
        one = af.grade_c5(self.ex5(sections=["REVENUE_NOTE"]), self.docs, self.rev(200e6, 100e6, currency="TWD"), market="TW")
        self.assertIsNone(one["value"]); self.assertEqual(one["calc"]["missing_sections"], ["BUSINESS_COMBINATIONS_NOTE"])
        self.assertEqual(af.issuer_market("ISSUER_TSMC"), "TW")             # TSM 的發行人在台股掛牌 → 兩處

    def test_older_c5_extraction_version_still_pairs_with_new_one(self):
        if not project_lock.read(): project_lock.acquire("t", "x")
        new = af.append_extraction(self.ex5(self.both(), [self.acq(), self.one()], extractor="A"), "p", self.docs, lock_holder="t")
        old = dict(new, extractor_id="B", extraction_id="EXT_OLD", rubric_version="C5_S4_SPEC_V0.1_SEC3.2")
        r = af.compare_dual(new, old, self.docs, revenue=self.rev(200e6, 100e6))
        self.assertEqual((r["value"], r["status"]), ("有", af.STATUS_VERIFIED))
        self.assertIn("RUBRIC_VERSION_MISMATCH", af.compare_dual(new, dict(old, rubric_version="C5_OTHER"), self.docs,
                                                                 revenue=self.rev(200e6, 100e6))["problems"])

    def test_c5_score_map_matches_rules(self):
        with open(os.path.join(self.old_root, "00_規範", "rules.json"), encoding="utf-8") as f: rules = json.load(f)
        self.assertEqual(rules["company"]["indicators"]["C5"]["rule"]["map"], af.C5_SCORE)

    # ---- 雙抽取
    def append(self, ex):
        if not project_lock.read(): project_lock.acquire("t", "x")
        return af.append_extraction(ex, "prompt-c5", self.docs, lock_holder="t")

    def test_dual_extraction_agreement_disagreement_and_null(self):
        yes1 = self.append(self.ex5(self.both(), [self.acq(), self.one()], extractor="A"))
        self.assertEqual(yes1["rubric_version"], af.RUBRIC_C5)
        yes2 = self.append(self.ex5(self.both(), [self.acq(), self.one()], extractor="B"))
        no2 = self.append(self.ex5([self.q("R2", "ACQUIRED_REVENUE", self.ACQ)], [self.acq()], extractor="C"))
        null2 = self.append(self.ex5(sections=["REVENUE_NOTE"], extractor="D"))
        rev = self.rev(200e6, 100e6)
        r = af.compare_dual(yes1, yes2, self.docs, revenue=rev)
        self.assertEqual((r["value"], r["status"]), ("有", af.STATUS_VERIFIED))
        r = af.compare_dual(yes1, no2, self.docs, revenue=rev)                  # 有 vs 無 → 取分數較低的「有」
        self.assertEqual((r["value"], r["status"]), ("有", af.STATUS_DISAGREE)); self.assertIn("REVIEW_DISAGREEMENT", r["detail"]["flags"])
        r = af.compare_dual(yes1, null2, self.docs, revenue=rev)
        self.assertIsNone(r["value"]); self.assertEqual(r["status"], af.STATUS_INSUFFICIENT)
        self.assertIsNone(af.compare_dual(yes1, yes2, self.docs)["value"])     # 沒有 C1 營收就算不出來
        self.assertIn("SAME_EXTRACTOR", af.compare_dual(yes1, dict(yes1, extraction_id="X"), self.docs, revenue=rev)["problems"])
        row = af.append_field_value(yes1, no2, self.docs, revenue=rev, lock_holder="t")
        self.assertEqual((row["value"], row["status"], row["rubric_version"]), ("有", af.STATUS_DISAGREE, af.RUBRIC_C5))
        again = af.recompute_value(row, af.read_jsonl(af.EXTRACTIONS), self.docs, revenue=rev)
        self.assertEqual((again["value"], again["status"]), (row["value"], row["status"]))

    def test_c1_revenue_pair_uses_the_same_rows_as_trend_c1(self):
        import data_spine, trend
        obs = data_spine.read_observations_checked()                           # 只讀專案的觀測值，不寫
        with open(os.path.join(self.old_root, "00_規範", "rules.json"), encoding="utf-8") as f: rules = json.load(f)
        cutoff = "2026-10-13T04:00:00+00:00"
        results = trend.evaluate_all(obs, rules, cutoff, current_snapshot_mode=False, preview_pending=False, mops_enabled=True)
        numeric = 0
        for issuer in ("ISSUER_ARISTA", "ISSUER_BROADCOM", "ISSUER_AURAS"):     # 三家全部必須與 C1 同源一致（含兩邊都是 null）
            pair, r = af.c1_revenue_pair(issuer, obs, cutoff, True), results.get(issuer) or {}
            if r.get("C1") is None:
                self.assertIsNone(pair, issuer)                                # 例：3324 去年同季財報在更補正清單、可用時間未知，兩邊都排除
                continue
            self.assertIsNotNone(pair, issuer)
            self.assertAlmostEqual((pair["current"] / pair["prior"] - 1) * 100, r["C1"], places=9, msg=issuer)
            self.assertEqual(pair["period_end"], r["latest_period_end"], issuer); numeric += 1
        self.assertGreaterEqual(numeric, 2)                                    # 至少兩家有實際數值可比


class I2QNextQuarterGuidance(AiFieldsBase):
    """T37：G-S4-3c。"""
    PREV = "For the current quarter we expect revenue between $90 million and $100 million."
    ACTUAL = "Revenue for the quarter was $105 million."
    UP = "For next quarter we expect revenue between $108 million and $118 million."
    KEEP = "For next quarter we expect revenue between $101 million and $105 million."
    DOWN = "For next quarter we expect revenue between $98 million and $102 million."
    POINT = "For next quarter we expect revenue of approximately $108 million."

    def ex(self, nxt=None, nxt_vals=(108, 118), prev_vals=(90, 100), actual=105, extractor="A", with_prev=True, **kw):
        quotes, facts = [], []
        def add(doc, typ, text, names_vals):
            quotes.append(self.q(doc, typ, text))
            for n, v in names_vals: facts.append({"name": n, "value": v, "unit": "USD million", "quote_index": len(quotes) - 1})
        if with_prev: add("R1", "PREVIOUS_QUARTER_GUIDANCE", self.PREV, [("previous_low", prev_vals[0]), ("previous_high", prev_vals[1])])
        if actual is not None: add("R2", "REPORTED_QUARTER_REVENUE", self.ACTUAL, [("reported_revenue", actual)])
        add("R2", "NEXT_QUARTER_GUIDANCE", nxt or self.UP, [("next_low", nxt_vals[0]), ("next_high", nxt_vals[1])])
        row = {"field": "I2Q", "issuer_id": ISSUER, "target_period": "2026-06-30", "doc_ids": ["R1", "R2"],
               "checklist": [{"doc_id": d, "checked": True} for d in ("R1", "R2")], "quotes": quotes, "facts": facts, "extractor_id": extractor}
        row.update(kw); return row

    def rev(self, current, currency="USD", period="2026-06-30"):
        return {"current": current, "prior": 1.0, "currency": currency, "period_end": period, "observation_ids": ["FIN_X"]}

    def test_up_keep_down_and_single_point(self):
        g = af.grade_i2q(self.ex(), self.docs); self.assertEqual((g["value"], g["reason"]), ("上修", "COMPUTED"))   # 105≥100 且 113>105
        self.assertEqual(g["calc"]["actual_source"], "QUOTE")
        self.assertEqual(af.grade_i2q(self.ex(self.KEEP, (101, 105)), self.docs)["value"], "維持")                  # 中點 103：不大於 105，也不小於 101.85
        self.assertEqual(af.grade_i2q(self.ex(self.DOWN, (98, 102)), self.docs)["value"], "下修")                   # 中點 100 < 105×0.97
        self.assertEqual(af.grade_i2q(self.ex(self.POINT, (108, 108)), self.docs)["value"], "上修")                 # 單點：上下緣相同
        g = af.grade_i2q(self.ex(actual=None), self.docs, self.rev(95e6))                                            # 實際 95：沒超過前次上緣 100 → 不是上修；新財測 113 不低於 → 維持
        self.assertEqual(g["value"], "維持"); self.assertFalse(g["calc"]["beat_upper"])

    def test_below_lower_end_is_down_even_if_next_guide_is_high(self):
        g = af.grade_i2q(self.ex(actual=None), self.docs, self.rev(85e6))           # 實際 85 < 前次下緣 90
        self.assertEqual(g["value"], "下修"); self.assertTrue(g["calc"]["below_lower"])

    def test_missing_any_of_the_three_is_null(self):
        g = af.grade_i2q(self.ex(with_prev=False), self.docs); self.assertEqual((g["value"], g["reason"]), (None, "I2Q_MISSING_ITEM"))
        self.assertEqual(g["calc"]["missing"], ["previous_low", "previous_high"])
        g = af.grade_i2q(self.ex(actual=None), self.docs); self.assertEqual(g["calc"]["missing"], ["actual_revenue"])
        no_next = self.ex(); no_next["facts"] = [f for f in no_next["facts"] if not f["name"].startswith("next_")]
        self.assertEqual(af.grade_i2q(no_next, self.docs)["calc"]["missing"], ["next_low", "next_high"])

    def test_ledger_revenue_has_priority_and_falls_back_to_the_quote(self):
        ex = self.ex(actual=None)                                   # 沒有引句營收
        g = af.grade_i2q(ex, self.docs, self.rev(105e6)); self.assertEqual((g["value"], g["calc"]["actual_source"]), ("上修", "LEDGER_FIN_REVENUE"))
        self.assertEqual(g["calc"]["revenue_observation_ids"], ["FIN_X"])
        # 帳本與引句不同 → 以帳本為準並旗標
        g = af.grade_i2q(self.ex(actual=105), self.docs, self.rev(85e6)); self.assertEqual((g["value"], g["flags"]), ("下修", ["ACTUAL_SOURCE_DIFFERS"]))
        # 幣別或期間對不上 → 不用帳本，退回引句；兩者都沒有 → null
        self.assertEqual(af.grade_i2q(self.ex(), self.docs, self.rev(1e6, currency="TWD"))["calc"]["actual_source"], "QUOTE")
        self.assertEqual(af.grade_i2q(self.ex(), self.docs, self.rev(1e6, period="2026-03-31"))["calc"]["actual_source"], "QUOTE")
        self.assertEqual(af.grade_i2q(ex, self.docs, self.rev(1e6, currency="TWD"))["reason"], "I2Q_MISSING_ITEM")

    def test_validation(self):
        self.assertEqual(af.validate_extraction(self.ex(), self.docs), [])
        for mutate, err in ((lambda e: e["facts"][0].update(unit="percent"), "I2Q_FACT_UNIT_INVALID:0:percent"),
                            (lambda e: e["facts"][0].update(name="latest_low"), "I2Q_FACT_NAME_INVALID:0:latest_low"),
                            (lambda e: e["facts"][0].update(quote_index=2), "I2Q_FACT_QUOTE_TYPE_MISMATCH:0"),
                            (lambda e: e["facts"][1].update(unit="TWD million"), "I2Q_CURRENCY_MISMATCH"),
                            (lambda e: e["facts"][0].update(value=120), "I2Q_RANGE_INVALID:previous_low"),
                            (lambda e: e["facts"][0].update(value=-1), "I2Q_FACT_VALUE_INVALID:0"),
                            (lambda e: e["facts"].append(dict(e["facts"][0])), "I2Q_FACT_DUPLICATE:previous_low")):
            e = self.ex(); mutate(e); self.assertIn(err, af.validate_extraction(e, self.docs), err)
        swapped = self.ex(); swapped["quotes"][0]["doc_id"] = "R2"       # 前次財測引自比新財測不早的文件
        swapped["quotes"][0]["quote"] = self.UP
        self.assertIn("I2Q_DOC_ORDER_INVALID", af.validate_extraction(swapped, self.docs))
        self.assertIn("QUOTE_TYPE_INVALID:0:AI_NUMERIC", af.validate_extraction(dict(self.ex(), quotes=[self.q("R2", "AI_NUMERIC", self.UP)], facts=[]), self.docs))

    def test_range_with_mixed_units_is_compared_after_scaling(self):
        mixed = self.ex()
        mixed["quotes"][0]["quote"] = "Alternatively net revenue in the range of $960 thousand to $1.01 million."
        mixed["facts"][0].update(value=960, unit="USD thousand"); mixed["facts"][1].update(value=1.01, unit="USD million")
        self.assertNotIn("I2Q_RANGE_INVALID:previous_low", af.validate_extraction(mixed, self.docs))     # 960 千 < 1.01 百萬
        mixed["facts"][0].update(value=1.2, unit="USD million")
        self.assertIn("I2Q_RANGE_INVALID:previous_low", af.validate_extraction(mixed, self.docs))        # 1.2 百萬 > 1.01 百萬

    def test_plus_minus_range_is_derived_from_center_and_spread(self):
        self.assertEqual(af.plus_minus_range_values("Revenue $50.0 billion ± $1.0 billion"), {"49", "51"})
        self.assertEqual(af.plus_minus_range_values("Net revenue is expected to be $2.700 billion +/- 5%."), {"2.565", "2.835"})
        self.assertEqual(af.plus_minus_range_values("revenue between $90 million and $100 million"), set())
        pct = self.ex("For next quarter we expect revenue of $100 million +/- 5%.", (95, 105))
        self.assertEqual(af.validate_extraction(pct, self.docs), [])
        self.assertEqual(af.grade_i2q(pct, self.docs)["value"], "下修")                  # 中點 100 < 105 × 0.97
        spread = self.ex("For next quarter we expect revenue of $110 million ± $10 million.", (100, 120))
        self.assertEqual(af.validate_extraction(spread, self.docs), [])
        self.assertEqual(af.grade_i2q(spread, self.docs)["value"], "上修")               # 中點 110 > 105
        wrong = self.ex("For next quarter we expect revenue of $100 million +/- 5%.", (96, 105))
        self.assertIn("FACT_VALUE_NOT_IN_QUOTE:3", af.validate_extraction(wrong, self.docs))   # 96 不是 100 ± 5% 的上下限

    def append(self, ex):
        if not project_lock.read(): project_lock.acquire("t", "x")
        return af.append_extraction(ex, "i2q-prompt", self.docs, lock_holder="t")

    def test_dual_extraction_and_field_value(self):
        a, b = self.append(self.ex(extractor="A")), self.append(self.ex(extractor="B"))
        self.assertEqual(a["rubric_version"], af.RUBRIC_I2Q)
        r = af.compare_dual(a, b, self.docs); self.assertEqual((r["value"], r["status"]), ("上修", af.STATUS_VERIFIED))
        down = self.append(self.ex(self.DOWN, (98, 102), extractor="C"))
        r = af.compare_dual(a, down, self.docs); self.assertEqual((r["value"], r["status"]), (None, af.STATUS_DISAGREE))
        self.assertIn("REVIEW_DISAGREEMENT", r["detail"]["flags"])
        none = self.append(self.ex(with_prev=False, extractor="D"))
        self.assertEqual(af.compare_dual(a, none, self.docs)["status"], af.STATUS_INSUFFICIENT)
        old = dict(b, rubric_version="I2Q_OTHER", extraction_id="EXT_OTHER", extractor_id="E")
        self.assertIn("RUBRIC_VERSION_MISMATCH", af.compare_dual(a, old, self.docs)["problems"])
        row = af.append_field_value(a, b, self.docs, revenue=self.rev(105e6), lock_holder="t")
        self.assertEqual((row["field"], row["value"], row["rubric_version"]), ("I2Q", "上修", af.RUBRIC_I2Q))
        again = af.recompute_value(row, af.read_jsonl(af.EXTRACTIONS), self.docs, revenue=self.rev(105e6))
        self.assertEqual((again["value"], again["status"]), (row["value"], row["status"]))

    def test_aggregates_alone_and_combined(self):
        i2q = {"A": {"value": "上修"}, "B": {"value": "上修"}, "C": {"value": "下修"}, "D": {"value": None}}
        r = af.aggregate_i2q(i2q); self.assertEqual((r["value"], r["n_comparable"], r["field"]), (1, 3, "I2Q"))
        self.assertIsNone(af.aggregate_i2q({"A": {"value": None}})["value"])
        i2 = {"C": {"value": "上修"}, "E": {"value": "下修"}}
        c = af.aggregate_i2_combined(i2, i2q)             # C 優先用年度財測的上修；D 沒有可比較的結果
        self.assertEqual(c["sources"], {"A": "I2Q", "B": "I2Q", "C": "I2", "E": "I2"})
        self.assertEqual((c["value"], c["n_comparable"], c["field"]), (3 - 1, 4, "I2+I2Q"))


class ScopeGuardAndVoids(AiFieldsBase):
    """T07c：scope 防呆（ai_field_values、ai_extractions）與欄位值作廢。"""
    A_Q = "AI accelerator revenue was $55 million this quarter"
    BAD_SCOPE = {"companies": ["VRT", "GEV"], "task_id": "T07"}

    def cx(self, extractor):
        return self.ex([self.q("R2", "AI_NUMERIC", self.A_Q)], extractor=extractor)

    def pair(self):
        if not project_lock.read(): project_lock.acquire("t", "x")
        return (af.append_extraction(self.cx("A"), "p", self.docs, lock_holder="t"),
                af.append_extraction(self.cx("B"), "p", self.docs, lock_holder="t"))

    def raw_value(self, good, scope):
        """模擬 T07 中斷時寫進帳本的錯誤列：繞過 append_row 直接追加（防呆上線前的狀態）。"""
        row = {k: v for k, v in good.items() if k not in ("row_sha256", "value_id")}
        row["scope"] = scope
        row["value_id"] = "AIV_BAD_" + str(len(af.read_jsonl(af.FIELD_VALUES)))
        row["row_sha256"] = af.sha256_bytes(af.canonical_line(row))
        with open(af.FIELD_VALUES, "ab") as f: f.write(af.canonical_line(row))
        return row

    def test_scope_errors_accepts_issuer_or_theme_only(self):
        self.assertEqual(af.scope_errors(ISSUER), []); self.assertEqual(af.scope_errors("theme_test"), [])
        for bad in (self.BAD_SCOPE, ["ISSUER_TEST"], None, "", "VRT", "ISSUER_NOPE", 3):
            self.assertTrue(af.scope_errors(bad), bad)
        self.assertTrue(af.scope_errors("theme_test", allow_themes=False))      # 抽取不能用主線當發行人

    def test_append_row_rejects_bad_scope_for_field_values(self):
        e1, e2 = self.pair()
        with self.assertRaisesRegex(ValueError, "INVALID_SCOPE"):
            af.append_field_value(e1, e2, self.docs, scope=self.BAD_SCOPE, lock_holder="t")
        with self.assertRaisesRegex(ValueError, "SCOPE_ISSUER_MISMATCH"):
            af.append_field_value(e1, e2, self.docs, scope="ISSUER_OTHER", lock_holder="t")
        with self.assertRaisesRegex(ValueError, "INVALID_SCOPE"):                 # 直接呼叫 append_row 也擋
            af.append_row(af.FIELD_VALUES, {"value_id": "AIV_X", "scope": self.BAD_SCOPE}, "value_id", lock_holder="t")
        self.assertEqual(af.read_jsonl(af.FIELD_VALUES), [])
        row = af.append_field_value(e1, e2, self.docs, lock_holder="t")
        self.assertEqual(row["scope"], ISSUER)
        theme = af.append_field_value(e1, e2, self.docs, scope="theme_test", lock_holder="t")
        self.assertEqual(theme["scope"], "theme_test")

    def test_append_row_rejects_unregistered_issuer_for_extractions(self):
        project_lock.acquire("t", "x")
        for bad in ("ISSUER_NOPE", "theme_test"):
            with self.assertRaisesRegex(ValueError, "INVALID_SCOPE"):
                af.append_row(af.EXTRACTIONS, dict(self.cx("A"), issuer_id=bad, extraction_id="EXT_" + bad), "extraction_id",
                              lock_holder="t")
        self.assertEqual(af.read_jsonl(af.EXTRACTIONS), [])

    def test_superseded_extraction_cannot_be_paired(self):
        e1, e2 = self.pair()
        corrected = af.append_extraction(dict(self.cx("B2"), supersedes_extraction_id=e2["extraction_id"]), "p", self.docs,
                                         lock_holder="t")
        self.assertEqual(af.superseded_extraction_ids(af.read_jsonl(af.EXTRACTIONS)), {e2["extraction_id"]})
        with self.assertRaisesRegex(ValueError, "EXTRACTION_SUPERSEDED"):
            af.append_field_value(e1, e2, self.docs, lock_holder="t")
        self.assertEqual(af.append_field_value(e1, corrected, self.docs, lock_holder="t")["value"], "A")

    def test_void_bad_scope_row_and_readers_ignore_it(self):
        e1, e2 = self.pair()
        good = af.append_field_value(e1, e2, self.docs, lock_holder="t")
        bad = self.raw_value(good, self.BAD_SCOPE)
        self.assertEqual(len(af.active_field_values()), 2)                       # 作廢前兩列都看得到
        v = af.append_field_value_void(bad["value_id"], "INVALID_SCOPE_ROW", "T07 中斷時寫入的錯誤 scope",
                                       replacement_value_id=good["value_id"], lock_holder="t")
        self.assertTrue(af.ledger_row_ok(v)); self.assertEqual(v["voided_by"], "t")
        self.assertEqual([r["value_id"] for r in af.active_field_values()], [good["value_id"]])
        self.assertEqual(af.voided_value_ids(), {bad["value_id"]})
        self.assertFalse(af.is_current(bad, self.docs)); self.assertTrue(af.is_current(good, self.docs))
        with self.assertRaisesRegex(ValueError, "FIELD_VALUE_VOIDED"):
            af.recompute_value(bad, af.read_jsonl(af.EXTRACTIONS), self.docs)
        self.assertEqual(af.recompute_value(good, af.read_jsonl(af.EXTRACTIONS), self.docs)["value"], "A")
        self.assertEqual(len(af.read_jsonl(af.FIELD_VALUES)), 2)                 # 原列保留，不刪不改

    def test_void_validation(self):
        e1, e2 = self.pair()
        good = af.append_field_value(e1, e2, self.docs, lock_holder="t")
        bad = self.raw_value(good, self.BAD_SCOPE)
        with self.assertRaises(PermissionError):                                  # 寫入須帶持鎖者名稱
            af.append_field_value_void(bad["value_id"], "INVALID_SCOPE_ROW", "n")
        cases = [((good["value_id"], "INVALID_SCOPE_ROW", "n"), {}, "VOID_REASON_NOT_APPLICABLE"),   # 合格列不能用此理由作廢
                 (("AIV_NONE", "INVALID_SCOPE_ROW", "n"), {}, "VOIDED_VALUE_NOT_FOUND"),
                 ((bad["value_id"], "OTHER", "n"), {}, "VOID_REASON_UNKNOWN"),
                 ((bad["value_id"], "INVALID_SCOPE_ROW", ""), {}, "VOID_NEEDS_VOIDED_BY_AND_NOTE"),
                 ((bad["value_id"], "INVALID_SCOPE_ROW", "n"), {"replacement_value_id": "AIV_NONE"}, "REPLACEMENT_NOT_FOUND"),
                 ((bad["value_id"], "INVALID_SCOPE_ROW", "n"), {"replacement_value_id": bad["value_id"]}, "REPLACEMENT_INVALID")]
        for args, kw, err in cases:
            with self.assertRaisesRegex(ValueError, err):
                af.append_field_value_void(*args, lock_holder="t", **kw)
        other = af.append_field_value(e1, e2, self.docs, scope="theme_test", lock_holder="t")
        with self.assertRaisesRegex(ValueError, "REPLACEMENT_MISMATCH"):
            e3 = af.append_extraction(self.cx("C"), "p", self.docs, lock_holder="t")
            diff = af.append_field_value(e1, e3, self.docs, lock_holder="t")
            af.append_field_value_void(bad["value_id"], "INVALID_SCOPE_ROW", "n", replacement_value_id=diff["value_id"], lock_holder="t")
        self.assertIsNotNone(other)
        af.append_field_value_void(bad["value_id"], "INVALID_SCOPE_ROW", "n", lock_holder="t")
        with self.assertRaisesRegex(ValueError, "VALUE_ALREADY_VOIDED"):
            af.append_field_value_void(bad["value_id"], "INVALID_SCOPE_ROW", "n2", lock_holder="t")
        self.assertEqual(len(af.read_jsonl(af.FIELD_VALUE_VOIDS)), 1)

    # ---- T20：欄位值標註
    BASIS = ["RUN_TEST", "T09_裁決.md"]

    def annotate(self, value, **kw):
        args = dict(field=value["field"], scope=value["scope"], extraction_ids=value["extraction_ids"], value_id=value["value_id"],
                    basis=self.BASIS, reviewer="claude", note="n", lock_holder="t")
        args.update(kw)
        return af.append_value_annotation(**args)

    def test_annotations_are_carried_into_report_reads(self):
        e1, e2 = self.pair()
        good = af.append_field_value(e1, e2, self.docs, lock_holder="t")
        theme = af.append_field_value(e1, e2, self.docs, scope="theme_test", lock_holder="t")
        a = self.annotate(good, independence="INDEPENDENCE_PARTIAL", verification="REVIEWER_SOURCE_RECOMPUTE_PASS")
        self.assertTrue(a["review_id"].startswith("REVA_")); self.assertTrue(af.ledger_row_ok(a)); self.assertNotIn("issuer_id", a)
        self.annotate(theme, independence="FULL")
        out = af.report_field_values()
        by = {v["value_id"]: v for v in out["values"]}
        self.assertEqual([(n["independence"], n["verification"]) for n in by[good["value_id"]]["annotations"]],
                         [("INDEPENDENCE_PARTIAL", "REVIEWER_SOURCE_RECOMPUTE_PASS")])
        self.assertEqual(by[theme["value_id"]]["annotations"][0]["independence"], "FULL")
        self.assertEqual(out["unattached_annotations"], [])
        # 標註不會被收進 review_ids：同一對抽取再寫一次，value_id 相同 → 重複拒絕；重播結果不變
        with self.assertRaisesRegex(ValueError, "DUPLICATE_ID"):
            af.append_field_value(e1, e2, self.docs, lock_holder="t")
        self.assertEqual(af.recompute_value(good, af.read_jsonl(af.EXTRACTIONS), self.docs)["value"], "A")
        with self.assertRaisesRegex(ValueError, "DUPLICATE_ID"):
            self.annotate(good, independence="INDEPENDENCE_PARTIAL", verification="REVIEWER_SOURCE_RECOMPUTE_PASS")

    def test_prior_round_visible_is_an_accepted_independence_level(self):
        e1, e2 = self.pair()
        good = af.append_field_value(e1, e2, self.docs, lock_holder="t")
        a = self.annotate(good, independence="PRIOR_ROUND_VISIBLE")
        self.assertEqual(a["independence"], "PRIOR_ROUND_VISIBLE")
        self.assertIsNone(a["verification"])                       # 不像 INDEPENDENCE_PARTIAL，不要求 verification
        row = next(v for v in af.report_field_values()["values"] if v["value_id"] == good["value_id"])
        self.assertEqual([n["independence"] for n in row["annotations"]], ["PRIOR_ROUND_VISIBLE"])     # 報告讀取時一併帶出
        self.assertEqual(sorted(af.INDEPENDENCE_LEVELS), ["FULL", "INDEPENDENCE_PARTIAL", "PRIOR_ROUND_VISIBLE"])
        r = self.annotate(good, independence="PRIOR_ROUND_VISIBLE", verification="RULE_CLARIFICATION_RESOLVED", note="n2")    # T30e：規則釐清後重抽
        self.assertEqual(r["verification"], "RULE_CLARIFICATION_RESOLVED")
        with self.assertRaisesRegex(ValueError, "INDEPENDENCE_UNKNOWN"):
            self.annotate(good, independence="PRIOR_ROUND")

    def test_coverage_annotation_without_value_row(self):
        if not project_lock.read(): project_lock.acquire("t", "x")
        e1 = af.append_extraction(self.cx("A"), "p", self.docs, lock_holder="t")
        n = af.append_value_annotation(field="C6", scope=ISSUER, extraction_ids=[e1["extraction_id"]], coverage="BOTH_NULL_NO_GUIDANCE_IN_DOCS",
                                       basis=self.BASIS, reviewer="claude", note="第二次抽取沒有官方引句", lock_holder="t")
        self.assertIsNone(n["value_id"])
        self.assertEqual([x["coverage"] for x in af.report_field_values()["unattached_annotations"]], ["BOTH_NULL_NO_GUIDANCE_IN_DOCS"])

    def test_annotation_validation(self):
        e1, e2 = self.pair()
        good = af.append_field_value(e1, e2, self.docs, lock_holder="t")
        bad = self.raw_value(good, self.BAD_SCOPE)
        af.append_field_value_void(bad["value_id"], "INVALID_SCOPE_ROW", "n", lock_holder="t")
        with self.assertRaises(PermissionError):
            self.annotate(good, independence="FULL", lock_holder=None)
        cases = [({"independence": "INDEPENDENCE_PARTIAL"}, "PARTIAL_NEEDS_VERIFICATION"),
                 ({}, "ANNOTATION_EMPTY"),
                 ({"independence": "HALF"}, "INDEPENDENCE_UNKNOWN"),
                 ({"independence": "FULL", "basis": []}, "ANNOTATION_NEEDS_BASIS"),
                 ({"independence": "FULL", "coverage": "BOTH_NULL_NO_GUIDANCE_IN_DOCS"}, "COVERAGE_ONLY_WITHOUT_VALUE"),
                 ({"independence": "FULL", "scope": "ISSUER_OTHER"}, "ANNOTATION_VALUE_MISMATCH"),
                 ({"independence": "FULL", "value_id": None}, "ANNOTATION_NEEDS_VALUE_ID"),
                 ({"value_id": None, "coverage": "BOTH_NULL_NO_GUIDANCE_IN_DOCS"}, "ACTIVE_VALUE_EXISTS_ANNOTATE_IT")]
        for kw, err in cases:
            with self.assertRaisesRegex(ValueError, err, msg=err):
                self.annotate(good, **kw)
        with self.assertRaisesRegex(ValueError, "ANNOTATED_VALUE_NOT_ACTIVE"):          # 作廢列不能標註
            self.annotate(bad, independence="FULL", scope=ISSUER)
        self.assertEqual(af.read_jsonl(af.REVIEWS), [])

    def test_superseded_by_newer_value_void(self):
        e1, e2 = self.pair()
        old = af.append_field_value(e1, e2, self.docs, lock_holder="t")
        e3 = af.append_extraction(self.cx("C"), "p", self.docs, lock_holder="t")
        new = af.append_field_value(e1, e3, self.docs, lock_holder="t", created_at="2999-01-01T00:00:00+08:00")
        with self.assertRaisesRegex(ValueError, "SUPERSEDE_NEEDS_REPLACEMENT"):
            af.append_field_value_void(old["value_id"], "SUPERSEDED_BY_NEWER_VALUE", "n", lock_holder="t")
        with self.assertRaisesRegex(ValueError, "REPLACEMENT_NOT_NEWER"):          # 不能拿較舊的列取代較新的
            af.append_field_value_void(new["value_id"], "SUPERSEDED_BY_NEWER_VALUE", "n", replacement_value_id=old["value_id"], lock_holder="t")
        with self.assertRaisesRegex(ValueError, "REPLACEMENT_MISMATCH:field_or_scope"):
            other = af.append_field_value(e1, e3, self.docs, scope="theme_test", lock_holder="t", created_at="2999-06-01T00:00:00+08:00")
            af.append_field_value_void(old["value_id"], "SUPERSEDED_BY_NEWER_VALUE", "n", replacement_value_id=other["value_id"], lock_holder="t")
        v = af.append_field_value_void(old["value_id"], "SUPERSEDED_BY_NEWER_VALUE", "規則改變後重抽", replacement_value_id=new["value_id"], lock_holder="t")
        self.assertEqual(v["reason_code"], "SUPERSEDED_BY_NEWER_VALUE")
        active = {x["value_id"] for x in af.active_field_values() if x["scope"] == ISSUER}
        self.assertEqual(active, {new["value_id"]})

    def test_tampered_void_ledger_is_rejected(self):
        e1, e2 = self.pair()
        good = af.append_field_value(e1, e2, self.docs, lock_holder="t")
        bad = self.raw_value(good, self.BAD_SCOPE)
        af.append_field_value_void(bad["value_id"], "INVALID_SCOPE_ROW", "n", lock_holder="t")
        row = dict(af.read_jsonl(af.FIELD_VALUE_VOIDS)[0], voided_value_id=good["value_id"])   # 改指向合格列
        with open(af.FIELD_VALUE_VOIDS, "wb") as f: f.write(af.canonical_line(row))
        with self.assertRaisesRegex(ValueError, "ROW_SHA256"): af.active_field_values()


class ExtractorRuleSummary(unittest.TestCase):
    """T20b：抽取者讀的規則摘要不得同時出現發行人名稱／代號與判定字樣（唯讀檢查專案實檔）。"""
    ROOT = af.ROOT
    SUMMARY = os.path.join(af.ROOT, "00_規範", "抽取規則摘要_S4.md")
    JUDGMENT_RE = r"上修|下修|維持|null|(?<![A-Za-z0-9_])(?:A|B|C|D|D1|D2)(?![A-Za-z0-9_])"

    @classmethod
    def issuer_terms(cls):
        with open(os.path.join(cls.ROOT, "01_共用資料", "data_spine", "entity_registry.json"), encoding="utf-8") as f: reg = json.load(f)
        terms = set()
        for i in reg["issuers"]:
            terms |= {i["issuer_id"], i["issuer_id"].replace("ISSUER_", "")}
            name = (i.get("issuer_name") or "").strip()
            if name: terms |= {name, name.split()[0].rstrip(",.")}
        for s in reg["securities"]:
            terms |= {str(x) for x in [s.get("ticker"), s.get("security_id")] + list(s.get("aliases") or []) if x}
        return {t for t in terms if len(t) >= 2}

    def hits(self, text):
        import re
        return sorted(t for t in self.issuer_terms()
                      if re.search(r"(?<![A-Za-z0-9_])" + re.escape(t) + r"(?![A-Za-z0-9_])", text, re.I))

    def test_summary_names_no_issuer_while_it_has_judgment_words(self):
        import re
        with open(self.SUMMARY, encoding="utf-8") as f: text = f.read()
        self.assertTrue(re.search(self.JUDGMENT_RE, text))                    # 摘要本來就含判定字樣
        self.assertEqual(self.hits(text), [])                                  # 所以不得出現任何發行人名稱或代號
        self.assertRegex(text, r"使用者決定紀錄\.md`：sha256 `[0-9a-f]{64}`")   # 檔頭附決定紀錄雜湊

    def test_detector_catches_a_named_expected_result(self):
        self.assertTrue(self.hits("例：Microsoft 應判維持"))
        self.assertTrue(self.hits("例：MU 從 B 變成 C"))
        self.assertEqual(self.hits("四家大型雲端業者；通用例：某公司財測上修"), [])


class RegisterDoc(AiFieldsBase):
    def test_register_doc_stores_raw_and_text_and_is_idempotent(self):
        project_lock.acquire("t", "x")
        raw = "<html><head><script>x=1</script></head><body><p>AI revenue was $5 million.</p><table><tr><td>A</td><td>B</td></tr></table></body></html>".encode()
        row = af.register_doc(ISSUER, "8-K-EX99", "https://example.test/x", raw, "ex99.htm",
                              official_published_at="2026-08-01T00:00:00+00:00", lock_holder="t")
        self.assertEqual(row["raw_sha256"], af.sha256_bytes(raw))
        text = af.load_doc_text(row)
        self.assertIn("AI revenue was $5 million.", text); self.assertNotIn("x=1", text)
        self.assertEqual(af.register_doc(ISSUER, "8-K-EX99", "https://example.test/x", raw, "ex99.htm",
                                         official_published_at="2026-08-01T00:00:00+00:00",
                                         lock_holder="t")["doc_id"], row["doc_id"])
        self.assertEqual(len(af.read_jsonl(af.DOCS_LEDGER)), 1)
        with open(os.path.join(af.ROOT, row["text_path"]), "ab") as f: f.write(b"tamper")
        with self.assertRaisesRegex(ValueError, "DOC_TEXT_HASH_MISMATCH"): af.load_doc_text(row)


class GuidanceFields(AiFieldsBase):
    def quotes(self, field="I2"):
        if field == "I2":
            return [self.q("R1", "PREVIOUS_GUIDANCE", "We expect full-year revenue of $10 billion to $12 billion"),
                    self.q("R2", "LATEST_GUIDANCE", "We now expect full-year revenue of $11.2 billion to $13.2 billion")]
        return [self.q("R1", "PREVIOUS_CAPEX_GUIDANCE", "We expect 2026 capital expenditures of $40 billion to $40 billion"),
                self.q("R2", "LATEST_CAPEX_GUIDANCE", "We now expect 2026 capital expenditures of $42 billion to $42 billion")]

    @staticmethod
    def facts(prev=(10, 12), latest=(11.2, 13.2), unit="USD billion"):
        return [{"name": "previous_low", "value": prev[0], "unit": unit, "quote_index": 0},
                {"name": "previous_high", "value": prev[1], "unit": unit, "quote_index": 0},
                {"name": "latest_low", "value": latest[0], "unit": unit, "quote_index": 1},
                {"name": "latest_high", "value": latest[1], "unit": unit, "quote_index": 1}]

    def gx(self, field="I2", extractor="A", facts=None, quotes=None, as_of="2026-08-15T00:00:00+00:00"):
        row = {"field": field, "issuer_id": ISSUER, "target_period": "FY2026", "doc_ids": ["R1", "R2"],
               "checklist": [{"doc_id": "R1", "checked": True}, {"doc_id": "R2", "checked": True}],
               "quotes": quotes if quotes is not None else self.quotes(field),
               "facts": facts if facts is not None else self.facts(), "extractor_id": extractor}
        if field == "I2": row["as_of"] = as_of
        return row

    def append(self, ex):
        if not project_lock.read(): project_lock.acquire("t", "x")
        return af.append_extraction(ex, "guidance-prompt", self.docs, lock_holder="t")

    def test_i2_validation_thresholds_units_and_freshness(self):
        ex = self.gx()
        self.assertEqual(af.validate_extraction(ex, self.docs), [])
        g = af.grade_i2(ex, self.docs)
        self.assertEqual(g["value"], "上修"); self.assertAlmostEqual(g["calc"]["change"], 1.2 / 11)
        flat_quotes = [self.quotes("I2")[0], self.q("R2", "LATEST_GUIDANCE",
                                                   "Our unchanged scenario is full-year revenue of $10.89 billion to $11.11 billion")]
        self.assertEqual(af.grade_i2(self.gx(facts=self.facts((10, 12), (10.89, 11.11)), quotes=flat_quotes), self.docs)["value"], "維持")
        self.assertEqual(af.grade_i2(self.gx(as_of="2027-01-01T00:00:00+00:00"), self.docs)["reason"], "LATEST_GUIDANCE_STALE")
        percent = self.gx(facts=self.facts((10, 12), (11.2, 13.2), "percent"))
        self.assertEqual(af.validate_extraction(percent, self.docs), [])
        self.assertIn("I2_FACT_SET_INCOMPLETE", af.validate_extraction(self.gx(facts=self.facts()[:-1]), self.docs))

    def test_i4_numeric_text_noncomparable_and_aggregate(self):
        numeric = self.gx("I4", facts=self.facts((40, 40), (42, 42)))
        self.assertEqual(af.grade_i4(numeric, self.docs)["value"], "上修")
        text = self.gx("I4", facts=[], quotes=[self.q("R2", "EXPLICIT_MAINTAIN",
                                                      "We are maintaining our prior capital expenditure outlook for 2026")])
        self.assertEqual(af.grade_i4(text, self.docs)["value"], "維持")
        noncomp = self.gx("I4", facts=[], quotes=[
            self.q("R1", "PREVIOUS_CAPEX_STATEMENT", "We expect 2026 capital expenditures of $40 billion to $40 billion"),
            self.q("R2", "LATEST_CAPEX_STATEMENT", "We now expect 2026 capital expenditures of $42 billion to $42 billion")])
        noncomp["null_reason"] = "NO_COMPARABLE_GUIDANCE"
        self.assertEqual(af.grade_i4(noncomp, self.docs)["reason"], "NO_COMPARABLE_GUIDANCE")
        self.assertIsNone(af.aggregate_i4({"A": {"value": "上修"}, "B": {"value": "維持"}})["value"])
        self.assertEqual(af.aggregate_i4({"A":{"value":"下修"},"B":{"value":"下修"},"C":{"value":"上修"}})["score"], -1)
        self.assertEqual(af.aggregate_i4({"A":{"value":"下修"},"B":{"value":"上修"},"C":{"value":"維持"}})["value"], "一家下修")

    # ---- G-S4-3a：I2 文字型的明確上修／下修
    PREV_TEXT = "We now expect our full-year 2026 revenue growth to be slightly above 40% year over year"
    RAISE = "We are raising our full-year 2026 revenue growth outlook to the mid-40s"

    def tx(self, *revisions, extractor="A"):
        return self.gx(extractor=extractor, facts=[], quotes=[self.q("R1", "PREVIOUS_GUIDANCE", self.PREV_TEXT)] +
                       [self.q("R2", "GUIDANCE_REVISION_TEXT", r) for r in revisions])

    def test_i2_slightly_above_40_to_mid_40s_raising_is_up(self):
        ex = self.tx(self.RAISE)
        self.assertEqual(af.validate_extraction(ex, self.docs), [])
        g = af.grade_i2(ex, self.docs)
        self.assertEqual((g["value"], g["reason"], g["calc"]["method"]), ("上修", "COMPUTED_TEXT_REVISION", "TEXT_REVISION"))
        down = af.grade_i2(self.tx("We are lowering our full-year 2026 revenue growth outlook to the mid-30s"), self.docs)
        self.assertEqual(down["value"], "下修")
        stale = self.gx(facts=[], quotes=ex["quotes"], as_of="2027-01-01T00:00:00+00:00")      # 120 天新鮮度仍適用
        self.assertEqual(af.grade_i2(stale, self.docs)["reason"], "LATEST_GUIDANCE_STALE")

    def test_i2_strong_wording_or_missing_period_is_not_a_revision(self):
        g = af.grade_i2(self.tx("We expect strong revenue growth in full-year 2026"), self.docs)      # 沒有和前次比較
        self.assertIsNone(g["value"]); self.assertEqual(g["reason"], "REVISION_TEXT_NOT_EXPLICIT")
        g = af.grade_i2(self.tx("We are raising our revenue growth outlook to the mid-40s"), self.docs)  # 沒寫目標期間
        self.assertIsNone(g["value"]); self.assertEqual(g["reason"], "REVISION_TEXT_NOT_EXPLICIT")
        g = af.grade_i2(self.tx(self.RAISE, "We are lowering our full-year 2026 revenue growth outlook to the mid-30s"), self.docs)
        self.assertIsNone(g["value"]); self.assertEqual(g["reason"], "REVISION_TEXT_CONFLICT")
        self.assertEqual(af.revision_text_direction("We are raising our FY26 outlook", "FY2026")["direction"], "上修")

    def test_i2_text_path_needs_revision_quote_and_no_partial_facts(self):
        only_prev = self.gx(facts=[], quotes=[self.q("R1", "PREVIOUS_GUIDANCE", self.PREV_TEXT)])
        self.assertIn("I2_FACT_SET_INCOMPLETE", af.validate_extraction(only_prev, self.docs))
        partial = self.tx(self.RAISE); partial["facts"] = [{"name": "previous_low", "value": 40, "unit": "percent", "quote_index": 0}]
        self.assertIn("I2_FACT_SET_INCOMPLETE", af.validate_extraction(partial, self.docs))
        both = self.gx(quotes=self.quotes("I2") + [self.q("R2", "GUIDANCE_REVISION_TEXT", self.RAISE)])   # 四個數字齊全 → 用數字
        self.assertEqual(af.grade_i2(both, self.docs)["calc"].get("method"), None)
        self.assertEqual(af.grade_i2(both, self.docs)["reason"], "COMPUTED")

    # ---- T20：as_of 防線
    def test_future_as_of_is_refused_for_extractions_and_values(self):
        if not project_lock.read(): project_lock.acquire("t", "x")
        late = self.gx(as_of="2026-10-17T23:30:00+08:00")
        with self.assertRaisesRegex(ValueError, r"FUTURE_AS_OF:as_of>created_at"):
            af.append_extraction(late, "p", self.docs, created_at="2026-10-17T22:34:46+08:00", lock_holder="t")
        far = af.now_iso()[:4]
        with self.assertRaisesRegex(ValueError, r"FUTURE_AS_OF:as_of>now\+5min"):
            af.append_extraction(self.gx(as_of=f"{int(far) + 1}-01-01T00:00:00+00:00"), "p", self.docs,
                                 created_at=f"{int(far) + 2}-01-01T00:00:00+00:00", lock_holder="t")
        with self.assertRaisesRegex(ValueError, "FUTURE_AS_OF:AS_OF_NO_TIMEZONE"):
            af.append_extraction(self.gx(as_of="2026-08-15T00:00:00"), "p", self.docs, lock_holder="t")
        with self.assertRaisesRegex(ValueError, r"FUTURE_AS_OF:as_of>available_at"):
            af.append_row(af.FIELD_VALUES, {"value_id": "AIV_X", "scope": ISSUER, "as_of": "2026-10-18T00:00:00+08:00",
                                            "available_at": "2026-10-17T00:00:00+08:00"}, "value_id", lock_holder="t")
        self.assertEqual(af.read_jsonl(af.EXTRACTIONS) + af.read_jsonl(af.FIELD_VALUES), [])
        ok = af.append_extraction(self.gx(as_of="2026-10-17T22:00:00+08:00"), "p", self.docs,
                                  created_at="2026-10-17T22:00:00+08:00", lock_holder="t")         # 等於 created_at 可以
        self.assertEqual(ok["as_of"], "2026-10-17T22:00:00+08:00")

    def raw_extraction(self, base, **kw):
        row = {k: v for k, v in base.items() if k != "row_sha256"}; row.update(kw)
        row["row_sha256"] = af.sha256_bytes(af.canonical_line(row))
        with open(af.EXTRACTIONS, "ab") as f: f.write(af.canonical_line(row))
        return row

    def test_as_of_metadata_annotation_and_report_read(self):
        if not project_lock.read(): project_lock.acquire("t", "x")
        a = af.append_extraction(self.gx(extractor="A"), "p", self.docs, lock_holder="t")
        late = self.raw_extraction(af.append_extraction(self.gx(extractor="B"), "p", self.docs, lock_holder="t"),
                                   extraction_id="EXT_LATE", as_of="2026-10-17T23:00:00+08:00", created_at="2026-10-17T22:49:50+08:00")
        value = af.append_field_value(a, late, self.docs, lock_holder="t")
        kw = dict(extraction_id="EXT_LATE", annotation_type="AS_OF_METADATA_ERROR", effective_as_of="2026-10-17T22:49:50+08:00",
                  direction="CONSERVATIVE_NO_LOOKAHEAD", basis=["as_of_scan.json#sha256=x"], reviewer="claude", note="n")
        with self.assertRaises(PermissionError): af.append_extraction_annotation(**kw)
        n = af.append_extraction_annotation(**kw, lock_holder="t")
        self.assertTrue(n["review_id"].startswith("REVX_")); self.assertNotIn("issuer_id", n)
        self.assertEqual((n["recorded_as_of"], n["effective_as_of"]), ("2026-10-17T23:00:00+08:00", "2026-10-17T22:49:50+08:00"))
        rep = af.report_field_values()
        row = next(v for v in rep["values"] if v["value_id"] == value["value_id"])
        self.assertEqual([x["type"] for x in row["extraction_annotations"]], ["AS_OF_METADATA_ERROR"])
        self.assertEqual(row["annotations"], [])
        with self.assertRaisesRegex(ValueError, "DUPLICATE_ID"): af.append_extraction_annotation(**kw, lock_holder="t")
        with self.assertRaisesRegex(ValueError, "DUPLICATE_ID"):          # 標註不會改變欄位值的 review_ids
            af.append_field_value(a, late, self.docs, lock_holder="t")

    def test_as_of_metadata_annotation_validation(self):
        if not project_lock.read(): project_lock.acquire("t", "x")
        ok = af.append_extraction(self.gx(extractor="A"), "p", self.docs, lock_holder="t")
        self.raw_extraction(ok, extraction_id="EXT_LATE", as_of="2026-10-17T23:00:00+08:00", created_at="2026-10-17T22:49:50+08:00")
        self.raw_extraction(ok, extraction_id="EXT_FIX", supersedes_extraction_id="EXT_LATE", extractor_id="C")
        base = dict(annotation_type="AS_OF_METADATA_ERROR", effective_as_of="2026-10-17T22:49:50+08:00",
                    direction="CONSERVATIVE_NO_LOOKAHEAD", basis=["b"], reviewer="claude", note="n", lock_holder="t")
        cases = [(dict(base, extraction_id=ok["extraction_id"], effective_as_of=ok["created_at"]), "AS_OF_NOT_LATER_THAN_CREATED_AT"),
                 (dict(base, extraction_id="EXT_LATE"), "EXTRACTION_SUPERSEDED"),
                 (dict(base, extraction_id="EXT_NONE"), "ANNOTATED_EXTRACTION_NOT_FOUND"),
                 (dict(base, extraction_id=ok["extraction_id"], direction="LOOKAHEAD"), "DIRECTION_UNKNOWN"),
                 (dict(base, extraction_id=ok["extraction_id"], annotation_type="OTHER"), "ANNOTATION_TYPE_UNKNOWN")]
        for kw, err in cases:
            with self.assertRaisesRegex(ValueError, err, msg=err): af.append_extraction_annotation(**kw)
        self.assertEqual(af.read_jsonl(af.REVIEWS), [])

    def test_scan_reports_existing_future_as_of_rows_without_rewriting(self):
        if not project_lock.read(): project_lock.acquire("t", "x")
        good = af.append_extraction(self.gx(), "p", self.docs, lock_holder="t")
        bad = dict(good, extraction_id="EXT_LATE", as_of="2026-10-17T23:30:00+08:00", created_at="2026-10-17T22:34:46+08:00")
        bad.pop("row_sha256"); bad["row_sha256"] = af.sha256_bytes(af.canonical_line(bad))
        with open(af.EXTRACTIONS, "ab") as f: f.write(af.canonical_line(bad))
        fix = dict(good, extraction_id="EXT_FIX", supersedes_extraction_id="EXT_LATE")
        with open(af.EXTRACTIONS, "rb") as f: before = f.read()
        hits = af.scan_future_as_of(af.read_jsonl(af.EXTRACTIONS) + [fix])
        self.assertEqual([(h["id"], h["superseded"]) for h in hits], [("EXT_LATE", True)])
        self.assertIn("FUTURE_AS_OF:as_of>created_at", hits[0]["errors"])
        with open(af.EXTRACTIONS, "rb") as f: self.assertEqual(f.read(), before)        # 只回報，不改寫

    # ---- G-S4-3b：字詞表收緊＋擴充
    def test_i2_lower_costs_does_not_cancel_a_raise_and_raise_capital_is_not_a_revision(self):
        g = af.grade_i2(self.tx("We raised our 2026 outlook on lower costs"), self.docs)
        self.assertEqual(g["value"], "上修")                                     # T19 版會因 lower 同時命中而判 null
        g = af.grade_i2(self.tx("We raised capital in 2026 to fund expansion"), self.docs)
        self.assertIsNone(g["value"]); self.assertEqual(g["reason"], "REVISION_TEXT_NOT_EXPLICIT")

    def test_i2_now_expect_is_not_a_revision(self):
        g = af.grade_i2(self.tx("We now expect our full-year 2026 revenue growth to be around 44%"), self.docs)
        self.assertIsNone(g["value"]); self.assertEqual(g["reason"], "REVISION_TEXT_NOT_EXPLICIT")

    def test_i2_each_new_revision_phrase(self):
        up = ["We revised up our 2026 revenue outlook", "Guidance for 2026 was revised upward",
              "Our 2026 revenue view was revised higher", "We raised full-year 2026 guidance to $12 billion",
              "2026 revenue is now higher than our previous outlook", "2026 revenue is now above prior guidance",
              "2026 年營收財測上調", "公司調升 2026 年營收財測"]
        down = ["We revised down our 2026 revenue outlook", "Guidance for 2026 was revised downward",
                "Our 2026 revenue view was revised lower", "We lowered full-year 2026 outlook to $9 billion",
                "2026 revenue is now below our previous outlook", "2026 年營收財測下調"]
        for text in up:
            self.assertEqual(af.revision_text_direction(text, "FY2026")["direction"], "上修", text)
        for text in down:
            self.assertEqual(af.revision_text_direction(text, "FY2026")["direction"], "下修", text)
        for text in ("We raise capital in 2026", "We lower costs in 2026", "2026 revenue was higher than prior year",
                     "2026 sales were above prior quarter levels", "We now expect 2026 revenue of $10 billion"):
            self.assertIsNone(af.revision_text_direction(text, "FY2026")["direction"], text)

    # ---- G-S4-5b：同一定義下的文字說法
    OUTSIDE = "Outside of this lease classification impact, our 2026 capital expenditure expectations remain unchanged"
    NO_EXCL = "Our 2026 capital expenditure expectations remain unchanged"

    def cx(self, typ, text):
        ex = self.dx(); ex["quotes"].append(self.q("R2", typ, text)); return ex

    def test_i4_microsoft_type_outside_of_remain_unchanged_is_maintain(self):
        ex = self.cx("COMPARABLE_EXPLICIT_MAINTAIN", self.OUTSIDE)
        self.assertEqual(af.validate_extraction(ex, self.docs), [])
        g = af.grade_i4(ex, self.docs)
        self.assertEqual((g["value"], g["reason"], g["calc"]["method"]), ("維持", "COMPARABLE_EXPLICIT_COMPARISON", "COMPARABLE_TEXT_EXPLICIT"))
        r = af.aggregate_i4({"MSFT": g, "META": {"value": "維持"}})
        self.assertIsNone(r["value"]); self.assertEqual(r["n_comparable"], 2)          # 仍低於 3 家門檻

    def test_i4_comparable_explicit_without_exclusion_word_is_not_comparable(self):
        g = af.grade_i4(self.cx("COMPARABLE_EXPLICIT_MAINTAIN", self.NO_EXCL), self.docs)
        self.assertIsNone(g["value"]); self.assertEqual(g["reason"], "NOT_COMPARABLE_DEFINITION_CHANGE")
        both = self.cx("COMPARABLE_EXPLICIT_MAINTAIN", self.OUTSIDE)
        both["quotes"].append(self.q("R2", "COMPARABLE_EXPLICIT_UP", self.OUTSIDE))
        self.assertEqual(af.grade_i4(both, self.docs)["reason"], "COMPARABLE_EXPLICIT_CONFLICT")
        no_def = self.cx("COMPARABLE_EXPLICIT_MAINTAIN", self.OUTSIDE)
        no_def["quotes"] = [q for q in no_def["quotes"] if q["type"] != "DEFINITION_CHANGE"]
        self.assertIn("I4_COMPARABLE_WITHOUT_DEFINITION_CHANGE", af.validate_extraction(no_def, self.docs))
        mixed = self.dx("COMPARABLE_CAPEX_GUIDANCE"); mixed["quotes"].append(self.q("R2", "COMPARABLE_EXPLICIT_MAINTAIN", self.OUTSIDE))
        self.assertIn("I4_COMPARABLE_NUMERIC_AND_EXPLICIT_BOTH_PRESENT", af.validate_extraction(mixed, self.docs))

    # ---- G-S4-5a：I4 資本支出定義改變
    PREV_CAPEX = "We expect 2026 capital expenditures of $40 billion to $40 billion"
    LEASE_SHIFT = "The shift from finance to operating leases adjusts our 2026 capital expenditure expectation to approximately $38 billion"
    DEF = "Finance leases are included in capital expenditures while operating leases are not"
    COMP = "On a comparable basis including finance leases, we expect 2026 capital expenditures of $43 billion to $43 billion"

    def dx(self, latest_type="LATEST_CAPEX_GUIDANCE", latest=None, definition=True, extractor="A"):
        quotes = [self.q("R1", "PREVIOUS_CAPEX_GUIDANCE", self.PREV_CAPEX),
                  self.q("R2", latest_type, latest or (self.COMP if latest_type == "COMPARABLE_CAPEX_GUIDANCE" else self.LEASE_SHIFT))]
        if definition: quotes.append(self.q("R2", "DEFINITION_CHANGE", self.DEF))
        value = 43 if latest_type == "COMPARABLE_CAPEX_GUIDANCE" else 38
        return self.gx("I4", extractor=extractor, quotes=quotes, facts=self.facts((40, 40), (value, value)))

    def test_i4_microsoft_type_definition_change_is_null(self):
        self.assertEqual(af.grade_i4(self.dx(definition=False), self.docs)["value"], "下修")    # 沒標定義改變時，機械判定會是下修
        ex = self.dx()
        self.assertEqual(af.validate_extraction(ex, self.docs), [])
        g = af.grade_i4(ex, self.docs)
        self.assertIsNone(g["value"]); self.assertEqual(g["reason"], "NOT_COMPARABLE_DEFINITION_CHANGE")
        only_def = self.gx("I4", facts=[], quotes=[self.q("R2", "DEFINITION_CHANGE", self.DEF),
                                                   self.q("R2", "EXPLICIT_MAINTAIN", "We are maintaining our prior capital expenditure outlook for 2026")])
        self.assertEqual(af.grade_i4(only_def, self.docs)["reason"], "NOT_COMPARABLE_DEFINITION_CHANGE")   # 文字說法不是可比數字
        self.assertIsNone(af.aggregate_i4({"A": g, "B": {"value": "上修"}, "C": {"value": "維持"}})["value"])  # 不計入，剩 2 家

    def test_i4_same_definition_comparable_numbers_are_used(self):
        ex = self.dx("COMPARABLE_CAPEX_GUIDANCE")
        self.assertEqual(af.validate_extraction(ex, self.docs), [])
        g = af.grade_i4(ex, self.docs)
        self.assertEqual((g["value"], g["calc"]["method"]), ("上修", "COMPARABLE_SAME_DEFINITION"))   # 40 → 43，+7.5%
        self.assertAlmostEqual(g["calc"]["change"], 0.075)
        self.assertIn("I4_COMPARABLE_WITHOUT_DEFINITION_CHANGE",
                      af.validate_extraction(self.dx("COMPARABLE_CAPEX_GUIDANCE", definition=False), self.docs))

    def test_guidance_old_rubric_extractions_still_pair(self):
        a = self.append(self.dx(extractor="A"))
        self.assertEqual(a["rubric_version"], af.RUBRIC_I4)
        old = dict(self.append(self.dx(extractor="B")), rubric_version="I4_S4_SPEC_V0.1_SEC3.4")
        r = af.compare_dual(a, old, self.docs)
        self.assertEqual((r["value"], r["status"]), (None, af.STATUS_INSUFFICIENT)); self.assertEqual(r["problems"], [])
        i2a, i2b = self.append(self.tx(self.RAISE, extractor="C")), self.append(self.tx(self.RAISE, extractor="D"))
        r = af.compare_dual(i2a, dict(i2b, rubric_version="I2_S4_SPEC_V0.1_SEC3.3"), self.docs)
        self.assertEqual((r["value"], r["status"]), ("上修", af.STATUS_VERIFIED))
        self.assertIn("RUBRIC_VERSION_MISMATCH", af.compare_dual(i2a, dict(i2b, rubric_version="I2_OTHER"), self.docs)["problems"])

    def test_i2_aggregate_dual_disagreement_and_rubrics(self):
        result = af.aggregate_i2({"A":{"value":"上修"},"B":{"value":"下修"},"C":{"value":"維持"}})
        self.assertEqual((result["value"], result["n_comparable"]), (0, 3)); self.assertIsNone(af.aggregate_i2({})["value"])
        a = self.append(self.gx("I4", extractor="A", facts=self.facts((40, 40), (42, 42))))
        b = self.append(self.gx("I4", extractor="B", facts=[], quotes=[self.q("R2", "EXPLICIT_MAINTAIN",
                                                                                "We are maintaining our prior capital expenditure outlook for 2026")]))
        r = af.compare_dual(a, b, self.docs)
        self.assertIsNone(r["value"]); self.assertEqual(r["status"], af.STATUS_DISAGREE)
        self.assertEqual(a["rubric_version"], af.RUBRIC_I4)
        i2 = self.append(self.gx("I2", extractor="C")); self.assertEqual(i2["rubric_version"], af.RUBRIC_I2)


if __name__ == "__main__":
    unittest.main(verbosity=1)
