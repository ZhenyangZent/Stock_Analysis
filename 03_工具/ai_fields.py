"""S4a：AI 閱讀欄位的帳本、引句驗證、雙抽取比對與 C6 判級。

原則（S4 規格 v0.1 第 1 節）：
- AI 只輸出「官方文件 ID、逐字引句、從引句讀出的數字」；等級由本程式依 C6 判定表 v0.3.2 計算（A → B → D1/D2 → C；B 需同句、且主要性字詞直接修飾 AI；SEGMENT_PROXY 類 A 需複核；v0.3.2：只有部門數字不判 B）。
- 引句必須在保存的文件純文字中逐字找到（NFKC 正規化＋合併空白後比對），找不到整筆作廢。
- 每個欄位由兩個不同的抽取者獨立抽取；一致 → AI_EXTRACTED_VERIFIED；等級型不一致 → 取較低等級＋REVIEW_DISAGREEMENT。
- 檢查清單未完成不得推定為 C；證據超出有效期間（最近 2 份財報）→ null。
- 三份帳本都是 append-only；寫入前須持有專案鎖。

帳本（01_共用資料/data_spine/）：docs_ledger.jsonl、ai_extractions.jsonl、ai_field_values.jsonl、
ai_field_value_voids.jsonl（T07c：欄位值作廢紀錄；被作廢的欄位值列一律不讀）。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import html as html_lib
import json
import os
import re
import subprocess
import unicodedata
from html.parser import HTMLParser

from common import ROOT, file_lock

SPINE_DIR = os.path.join(ROOT, "01_共用資料", "data_spine")
DOCS_LEDGER = os.path.join(SPINE_DIR, "docs_ledger.jsonl")
EXTRACTIONS = os.path.join(SPINE_DIR, "ai_extractions.jsonl")
FIELD_VALUES = os.path.join(SPINE_DIR, "ai_field_values.jsonl")
REVIEWS = os.path.join(SPINE_DIR, "ai_reviews.jsonl")
RAW_DOCS = os.path.join(SPINE_DIR, "raw", "docs")
# T07c（2026-10-16）：append-only 帳本不刪列；格式錯誤的欄位值以「作廢紀錄」標記，所有讀取欄位值的程式都必須略過被作廢的列
FIELD_VALUE_VOIDS = os.path.join(SPINE_DIR, "ai_field_value_voids.jsonl")
VOID_REASONS = {"INVALID_SCOPE_ROW", "SUPERSEDED_BY_NEWER_VALUE"}   # 後者：同欄位同 scope 有較新的欄位值（規則或複核結果改變後重抽）
# scope 只能是登錄的發行人 ID（entity_registry.json 的 issuers）或主線 ID（rules.json 的 themes 鍵）
ENTITY_REGISTRY = os.path.join(SPINE_DIR, "entity_registry.json")
RULES_JSON = os.path.join(ROOT, "00_規範", "rules.json")

RUBRIC_C6 = "C6_TABLE_V0.3.2"
RUBRIC_C5 = "C5_S4_SPEC_V0.1_SEC3.2_G-S4-2ab"   # S4 規格 v0.1 第 3.2 節＋G-S4-2a（百分比）＋G-S4-2b（台股兩處）
# 同一套判斷規則可接受的抽取版本：G-S4-2a／2b 只新增百分比數字與台股清單，不改變既有抽取的判讀，所以舊版抽取仍可比對。
C5_EXTRACTION_RUBRICS = {"C5_S4_SPEC_V0.1_SEC3.2", RUBRIC_C5}
RUBRIC_I2 = "I2_S4_SPEC_V0.1_SEC3.3_G-S4-3ab"  # ＋G-S4-3a：文字型的明確上修／下修；G-S4-3b（2026-10-17）：字詞表收緊＋擴充
RUBRIC_I2Q = "I2Q_S4_SPEC_V0.1_G-S4-3c"        # G-S4-3c（2026-10-19）：下一季財測「財報優於預期＋上調財測」型；影子並列，不計入正式分數
RUBRIC_I4 = "I4_S4_SPEC_V0.1_SEC3.4_G-S4-5ab"  # ＋G-S4-5a：資本支出定義改變 → 不可比；G-S4-5b（2026-10-17）：同一定義下的文字說法
# G-S4-3a／3b／5a／5b 只新增引句類型或調整字詞表，抽取格式相容，所以舊版抽取仍可比對（與 C5 相同的處理）。
I2_EXTRACTION_RUBRICS = {"I2_S4_SPEC_V0.1_SEC3.3", "I2_S4_SPEC_V0.1_SEC3.3_G-S4-3a", RUBRIC_I2}
I4_EXTRACTION_RUBRICS = {"I4_S4_SPEC_V0.1_SEC3.4", "I4_S4_SPEC_V0.1_SEC3.4_G-S4-5a", RUBRIC_I4}
# v0.3.2 只改判級（部門數字單獨出現不再判 B），抽取格式與 v0.3.1 相同：v0.3.1 的抽取可直接用 v0.3.2 重新判級，不必重抽。
C6_EXTRACTION_RUBRICS = {"C6_TABLE_V0.3.1", "C6_TABLE_V0.3.2"}
STATUS_VERIFIED, STATUS_DISAGREE, STATUS_INSUFFICIENT = "AI_EXTRACTED_VERIFIED", "REVIEW_DISAGREEMENT", "DATA_INSUFFICIENT"
EXTRACTOR_MODEL_FAMILIES = {"ANTHROPIC", "OPENAI", "GOOGLE", "META", "MISTRAL", "OTHER", "UNKNOWN"}

# 官方文件類型（S4 規格 1.4）。新聞、分析師、Podcast 不在其中。
OFFICIAL_DOC_TYPES = {"10-K", "10-Q", "8-K", "8-K-EX99", "20-F", "6-K", "IR-PRESENTATION", "IR-TRANSCRIPT",
                      "MOPS-FINANCIAL", "MOPS-MATERIAL-INFO", "MOPS-INVESTOR-CONFERENCE"}
# 「財報」類：用來界定有效期間（最近 2 份已發布財報）。同一 period_end 的 10-Q／財報新聞稿算同一份財報。
REPORT_DOC_TYPES = {"10-K", "10-Q", "20-F", "8-K-EX99-EARNINGS", "MOPS-FINANCIAL"}
OFFICIAL_DOC_TYPES.add("8-K-EX99-EARNINGS")

CALL_DOC_TYPES = {"IR-PRESENTATION", "IR-TRANSCRIPT", "MOPS-INVESTOR-CONFERENCE"}   # 法說簡報／逐字稿
C6_QUOTE_TYPES = {"AI_NUMERIC", "SEGMENT_NUMBER", "SEGMENT_AI_DRIVEN", "AI_MAIN_DRIVER_NO_NUMBER",
                  "AI_NOT_YET_REVENUE", "AI_NEGLIGIBLE", "AI_MENTION_NO_PRIMACY"}
CALL_UNAVAILABLE_CODE = "CALL_MATERIALS_UNAVAILABLE_AUTOMATED"      # v0.3 修改 3
# G-S4-C6s（2026-10-18）：台股公司用 MOPS 官方法說會查詢，在有效期間內查不到任何場次，附查詢條件與時間 → 比照美股判 C_WITHOUT_CALL_MATERIALS
CALL_NONE_LISTED_CODE = "MOPS_CONFERENCE_NONE_LISTED"
# G-S4-C6q（2026-10-18）：AI_NUMERIC 的數字只能計算 AI 本身；與伺服器、資料中心、雲端等混合的類別要記為 SEGMENT_NUMBER。
# 程式只擋最明顯的連接寫法（AI 與其他類別用 及／與／和／、／and／& 連在一起）；其餘由抽取者與複查者判斷。
_MIX_OTHER = r"(?:伺服器|資料中心|雲端|server|data\s?center|cloud)"
_MIX_CONJ = r"(?:及|與|和|、|and|&)"
# G-S4-C6t（2026-10-19）：SEGMENT_NUMBER 必須是營收金額，或占營收的比重；引句裡的數字全部是成長率（季增、年增、倍數、y/y、q/q）→ 不算。
_NUM_RE = re.compile(r"[$＄]?\d[\d,]*(?:\.\d+)?\s*(?:%|％|倍|x\b)?", re.I)
_GROWTH_LEFT_RE = re.compile(r"(?:季增|年增|月增|增加|增長|成長|減少|衰退|QoQ|YoY|q/q|y/y|grew|growth|grow|increased?|decreased?|up|down|\+)\W{0,3}(?:約|超過|逾|達|for)?\s*$", re.I)
_GROWTH_RIGHT_RE = re.compile(r"^\W{0,3}(?:y/y|q/q|yoy|qoq|year-over-year|quarter-over-quarter|同比|年增|季增|成長|增長)", re.I)
_PERIOD_LEFT_RE = re.compile(r"(?<![A-Za-z])(?:[12]?H|Q|FY|CY)$", re.I)
_PERIOD_RIGHT_RE = re.compile(r"^\s?(?:年|季|月|日|號|Q|H\d?|週)")


_PM_RE = re.compile(r"\$?\s*(\d[\d,]*(?:\.\d+)?)\s*(?:billion|million)?\s*(?:±|\+/-|\+-)\s*\$?\s*(\d[\d,]*(?:\.\d+)?)\s*(%|billion|million)?", re.I)


def plus_minus_range_values(quote: str) -> set:
    """「$50.0 billion ± $1.0 billion」「$2.700 billion +/- 5%」→ 下限、上限的字串集合（與 number_tokens 比對用）；沒有 ± 寫法回傳空集合。"""
    out = set()
    for m in _PM_RE.finditer(normalize(quote)):
        try:
            center, delta = float(m.group(1).replace(",", "")), float(m.group(2).replace(",", ""))
        except ValueError:
            continue
        if m.group(3) == "%": delta = center * delta / 100.0
        for v in (center - delta, center + delta): out.add(f"{round(v, 6):g}")
    return out


def segment_number_is_growth_only(quote: str) -> bool:
    """去掉期間記號（2026年、Q2、1H26）後，剩下的數字全部是成長率形式（帶 % 或倍，且旁邊有成長字詞）→ True。"""
    text = normalize(quote)
    growth, substantive = 0, 0
    for m in _NUM_RE.finditer(text):
        token = m.group(0)
        digits = re.sub(r"\D", "", token)
        if not digits: continue
        left, right = text[max(0, m.start() - 14):m.start()], text[m.end():m.end() + 24]
        if _PERIOD_RIGHT_RE.match(right) or _PERIOD_LEFT_RE.search(text[max(0, m.start() - 4):m.start()]) \
                or (re.fullmatch(r"(?:19|20)\d\d", digits) and "," not in token and "%" not in token):
            continue                                              # 期間記號，不是部門數字
        is_pct = bool(re.search(r"[%％倍]|x$", token, re.I))
        if is_pct and (_GROWTH_LEFT_RE.search(left) or _GROWTH_RIGHT_RE.search(right)):
            growth += 1
        else:
            substantive += 1
    return growth > 0 and substantive == 0


AI_MIXED_CATEGORY_RE = re.compile(
    r"(?:(?<![A-Za-z])AI(?![a-z])|人工智慧)[^,，。;；.]{0,14}?" + _MIX_CONJ + r"\s*" + _MIX_OTHER + "|"
    + _MIX_OTHER + r"\s*" + _MIX_CONJ + r"\s*(?:(?<![A-Za-z])AI(?![a-z])|人工智慧)", re.I)
ALLOWED_LIMITATIONS = {"PRODUCT_MIX_IMAGE_NOT_OCR"}                # 抽取者可註記的限制；會帶進結果
REVIEW_FLAG_SEGMENT_PROXY = "SEGMENT_PROXY_REVIEWED"
# T30g：部門代理複核不通過（原因碼綁在複核列上）。複核不通過時，這組部門數字＋AI 驅動句仍判 B，並標 SEGMENT_PROXY_REVIEW_FAILED。
REVIEW_FLAG_SEGMENT_PROXY_FAILED = "SEGMENT_PROXY_REVIEW_FAILED"
REVIEW_FAIL_REASONS = {"GROWTH_ATTRIBUTION_ONLY"}      # 只說「成長」主要來自 AI，沒有說部門營收本身主要由 AI 構成
# T20（T09 裁決）：欄位值層級的標註，寫在 ai_reviews.jsonl；不帶 issuer_id，所以不會被 C6 的 SEGMENT_PROXY 複核或 review_ids 收進去
REVIEW_FLAG_ANNOTATION = "FIELD_VALUE_ANNOTATION"
# PRIOR_ROUND_VISIBLE（T26，Claude 代決）：抽取者看過上一輪的比對結果，但沒看過新文件被抽取的內容；嚴重程度介於 FULL 與 INDEPENDENCE_PARTIAL 之間
INDEPENDENCE_LEVELS = {"FULL", "PRIOR_ROUND_VISIBLE", "INDEPENDENCE_PARTIAL"}
VERIFICATION_CODES = {"REVIEWER_SOURCE_RECOMPUTE_PASS", "RULE_CLARIFICATION_RESOLVED"}   # 後者：兩次抽取的分歧來自規則不明，已由裁決釐清並依新規則重抽
COVERAGE_CODES = {"BOTH_NULL_NO_GUIDANCE_IN_DOCS"}       # 沒有欄位值列可掛（例：第二次抽取因無官方引句而寫不出）
AS_OF_CLOCK_SKEW_MINUTES = 5                            # T20：as_of 不得晚於現在時間＋5 分鐘
# T20b：抽取層標註（寫在 ai_reviews.jsonl；同樣不帶 issuer_id）。as_of 晚於 created_at 只會讓資料更晚可見，方向保守。
REVIEW_FLAG_EXTRACTION_ANNOTATION = "EXTRACTION_ANNOTATION"
EXTRACTION_ANNOTATION_TYPES = {"AS_OF_METADATA_ERROR"}
AS_OF_DIRECTIONS = {"CONSERVATIVE_NO_LOOKAHEAD"}
GRADE_SCORE = {"A": 1, "B": 0, "C": 0, "D1": -1, "D2": -1}
# C5：AI 只抽「併購營收」「一次性營收」的數字與引句；「有／無」由 grade_c5 依 ΔRev（取自 C1 的營收資料）計算。
C5_QUOTE_TYPES = {"ACQUIRED_REVENUE", "ONE_TIME_REVENUE", "NO_MATERIAL_ACQUISITION"}
C5_FACTS = {"acquired_revenue": "ACQUIRED_REVENUE", "one_time_revenue": "ONE_TIME_REVENUE",   # 數字 → 必須對應的引句類型
            # G-S4-2a（2026-10-16）：只揭露「併購貢獻佔去年同季營收的百分比」時，程式以 C1 同源的去年同季營收換算金額
            "acquired_revenue_pct_of_prior_year": "ACQUIRED_REVENUE"}
C5_PCT_FACT = "acquired_revenue_pct_of_prior_year"
C5_SECTIONS = {"BUSINESS_COMBINATIONS_NOTE", "REVENUE_NOTE", "MDNA_OR_OPERATIONS"}             # 美股：最新一份財報要查的三處
C5_SECTIONS_TW = {"BUSINESS_COMBINATIONS_NOTE", "REVENUE_NOTE"}                                  # G-S4-2b：台股季報只查兩處
C5_FILING_TYPES = {"10-Q", "10-K", "20-F", "MOPS-FINANCIAL"}                                  # 「最新一份財報」（不含財報新聞稿）
C5_MAJORITY = 0.5                  # G-S4-1：「主要」＝過半（X ≥ 0.5 × ΔRev）
C5_DECLINE_MATERIALITY = 0.02      # G-S4-2：衰退時，X ≥ 去年同季營收 2% 視為併購掩蓋衰退
C5_SCORE = {"無": 0, "有": -1}      # 與 rules.json company.indicators.C5 的 map 相同
I2_QUOTE_TYPES = {"PREVIOUS_GUIDANCE", "LATEST_GUIDANCE", "GUIDANCE_REVISION_TEXT"}
I4_QUOTE_TYPES = {"PREVIOUS_CAPEX_GUIDANCE", "LATEST_CAPEX_GUIDANCE", "PREVIOUS_CAPEX_STATEMENT", "LATEST_CAPEX_STATEMENT",
                  "EXPLICIT_UP", "EXPLICIT_MAINTAIN", "EXPLICIT_DOWN",
                  "DEFINITION_CHANGE",            # G-S4-5a：官方說明兩次數字的差異來自會計分類／定義改變
                  "COMPARABLE_CAPEX_GUIDANCE",    # G-S4-5a 例外：公司另給的「同一定義」可比數字
                  # G-S4-5b：明確排除定義改變影響、並寫出比較方向的官方文字（方向以類型為準，程式驗證排除字詞）
                  "COMPARABLE_EXPLICIT_UP", "COMPARABLE_EXPLICIT_MAINTAIN", "COMPARABLE_EXPLICIT_DOWN"}
COMPARABLE_EXPLICIT = {"COMPARABLE_EXPLICIT_UP": "上修", "COMPARABLE_EXPLICIT_MAINTAIN": "維持", "COMPARABLE_EXPLICIT_DOWN": "下修"}
DEFINITION_EXCLUSION_RE = re.compile(r"\boutside of\b|\bexcluding\b|\baside from\b|不含|排除", re.I)
# G-S4-3a：方向由程式比對字詞表判定，不由 AI 判斷。引句必須同時寫出修正方向與同一目標期間
# （程式的可操作定義：引句含 target_period 的西元年，例如 2026，或 FY26）。同時出現上修與下修字詞 → 不判定。
# G-S4-3b：raise／lower 必須接財測受詞（our／the … guidance|outlook|forecast|range，中間最多 4 個字），
# 避免「lower costs」「raise capital」誤中；不收 now expect（沒有說相對前次是高或低）。
# previous／prior 後面若接 year／quarter／period 是同比，不是相對前次財測，排除。
_OBJ = r"(?:guidance|outlook|forecast|range)\b"
_VS_PRIOR = r"(?:our\s+)?(?:previous|prior)\b(?!\s+(?:year|quarter|period)s?\b)"
REVISION_UP_RE = re.compile("|".join([
    r"\b(?:rais|increas)(?:e|es|ed|ing)\s+(?:our|the)\s+(?:[\w-]+\s+){0,4}?" + _OBJ,
    r"\brais(?:ed|ing)\s+(?:[\w-]+\s+){0,4}?" + _OBJ + r"\s+to\b",
    r"\brevised\s+(?:up|upward|upwards|higher)\b",
    r"\bhigher\s+than\s+" + _VS_PRIOR, r"\babove\s+" + _VS_PRIOR,
    r"\bup from\b", r"上修|調高|上調|調升"]), re.I)
REVISION_DOWN_RE = re.compile("|".join([
    r"\b(?:lower|reduc)(?:e|es|s|ed|ing)?\s+(?:our|the)\s+(?:[\w-]+\s+){0,4}?" + _OBJ,
    r"\blower(?:ed|ing)\s+(?:[\w-]+\s+){0,4}?" + _OBJ + r"\s+to\b",
    r"\brevised\s+(?:down|downward|downwards|lower)\b",
    r"\bbelow\s+" + _VS_PRIOR,
    r"\bdown from\b", r"下修|調降|下調"]), re.I)
GUIDANCE_FACTS = {"previous_low", "previous_high", "latest_low", "latest_high"}
I2_FRESH_DAYS = 120                 # G-S4-3
# G-S4-3c：I2Q。三樣東西——前次財測（對剛公布這一季）、實際營收（優先帳本 FIN_REVENUE，找不到才用引句）、新財測（對下一季）。
I2Q_QUOTE_TYPES = {"PREVIOUS_QUARTER_GUIDANCE", "NEXT_QUARTER_GUIDANCE", "REPORTED_QUARTER_REVENUE"}
I2Q_FACTS = {"previous_low", "previous_high", "next_low", "next_high", "reported_revenue"}
I2Q_FACT_QUOTE = {"previous_low": "PREVIOUS_QUARTER_GUIDANCE", "previous_high": "PREVIOUS_QUARTER_GUIDANCE",
                  "next_low": "NEXT_QUARTER_GUIDANCE", "next_high": "NEXT_QUARTER_GUIDANCE", "reported_revenue": "REPORTED_QUARTER_REVENUE"}
I2Q_DOWN_TOLERANCE = 0.03           # 新財測中點 < 實際營收 × (1 − 3%) → 下修
I2Q_ACTUAL_MISMATCH_TOLERANCE = 0.02   # 帳本營收與引句營收相差超過 2% → 旗標 ACTUAL_SOURCE_DIFFERS（仍以帳本為準）
I2_MOVE_THRESHOLD = 0.01            # G-S4-4：中點變動 ±1%
I4_MOVE_THRESHOLD = 0.03            # G-S4-5：中點變動 ±3%
UNIT_SCALES = {"": 1.0, "thousand": 1e3, "million": 1e6, "billion": 1e9}
CURRENCIES = {"USD", "TWD"}
GRADE_RANK = {"A": 4, "B": 3, "C": 2, "D1": 1, "D2": 0}      # 數字越小越保守（證據越少／越負面）
MIN_QUOTE_CHARS = 12
# 文件本身使用的 AI 字樣（判定表「名詞」）。「資料中心」「雲端」「高效能運算」不算。
AI_TERM_RE = re.compile(r"(?<![A-Za-z])AI(?![a-z])|人工智慧|人工智能|生成式|artificial intelligence|generative ai", re.I)
PRIMACY_RE = re.compile(r"\b(?:primary|primarily|main|mainly|largest|majority|most of|driven primarily)\b|主要係|主要|主係|主因|最大|多數|過半|大部分", re.I)
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?;])\s+|(?<=[。！？；])\s*")
# v0.3.1：主要性字詞必須「直接修飾」AI。程式的可操作定義：同一句內，主要性字詞與 AI 字樣之間最多隔 5 個英文單字
# （任一順序，例：primarily driven by AI、AI is the main driver），或相距不超過 10 個字元（中文，例：主要來自 AI）。
# 隔更遠的情況（例：primarily due to increases in bit shipments and prices driven by accelerating AI demand）不算。
_AI = AI_TERM_RE.pattern
_PRIM_EN = r"\b(?:driven primarily|primary|primarily|main|mainly|largest|majority|most of)\b"
_PRIM_CN = r"(?:主要係|主要|主係|主因|最大|多數|過半|大部分)"      # G-S4-C6r：主係／主因／主要係 與「主要」同義
PRIMACY_ON_AI_RE = re.compile("|".join([
    _PRIM_EN + r"(?:\W+\w+){0,5}?\W+(?:" + _AI + ")",
    "(?:" + _AI + r")(?:\W+\w+){0,5}?\W+" + _PRIM_EN,
    _PRIM_CN + r"[^。！？.!?;；]{0,10}?(?:" + _AI + ")",
    "(?:" + _AI + r")[^。！？.!?;；]{0,10}?" + _PRIM_CN]), re.I)


def sentence_has_ai_and_primacy(text: str) -> bool:
    """v0.3.1：主要性字詞必須在同一句，且直接修飾 AI（見 PRIMACY_ON_AI_RE）。"""
    return any(PRIMACY_ON_AI_RE.search(sentence) for sentence in SENTENCE_SPLIT_RE.split(normalize(text)))


def quote_hash(quote: str) -> str:
    return sha256_bytes(normalize(quote).encode("utf-8"))


def effective_type(quote: dict) -> str:
    """AI_MAIN_DRIVER_NO_NUMBER／SEGMENT_AI_DRIVEN 的引句若同句沒有 AI 字樣＋主要性字詞，記為
    AI_MENTION_NO_PRIMACY，不能用來判級。"""
    if quote.get("type") in ("AI_MAIN_DRIVER_NO_NUMBER", "SEGMENT_AI_DRIVEN") and not sentence_has_ai_and_primacy(quote.get("quote", "")):
        return "AI_MENTION_NO_PRIMACY"
    return quote.get("type")


# ----------------------------------------------------------------------------
# 基本工具
# ----------------------------------------------------------------------------
def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""): h.update(chunk)
    return h.hexdigest()


def canonical_line(row: dict) -> bytes:
    return (json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def normalize(text: str) -> str:
    """NFKC 正規化並合併所有空白；統一各式引號與破折號，避免 HTML 轉文字後的排版差異造成誤判。"""
    text = unicodedata.normalize("NFKC", text or "")
    for a, b in (("‘", "'"), ("’", "'"), ("“", '"'), ("”", '"'), ("–", "-"), ("—", "-"),
                 (" ", " "), ("​", "")):
        text = text.replace(a, b)
    return re.sub(r"\s+", " ", text).strip()


def quote_in_text(quote: str, text: str) -> bool:
    q = normalize(quote)
    return len(q) >= MIN_QUOTE_CHARS and q in normalize(text)


def now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def parse_time(value):
    return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00")) if value else None


def read_jsonl(path: str) -> list:
    if not os.path.exists(path): return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(x) for x in f.read().splitlines() if x.strip()]


def known_scopes() -> tuple:
    """（發行人 ID 集合, 主線 ID 集合）。檔案缺漏時回傳空集合 → 一律拒絕寫入。"""
    issuers, themes = set(), set()
    if os.path.exists(ENTITY_REGISTRY):
        with open(ENTITY_REGISTRY, encoding="utf-8") as f:
            issuers = {i["issuer_id"] for i in json.load(f).get("issuers", []) if isinstance(i.get("issuer_id"), str)}
    if os.path.exists(RULES_JSON):
        with open(RULES_JSON, encoding="utf-8") as f:
            themes = set((json.load(f).get("themes") or {}).keys())
    return issuers, themes


def scope_errors(scope, allow_themes: bool = True) -> list:
    """scope 必須是單一字串：登錄的發行人 ID，或（allow_themes 時）主線 ID。物件、清單、股票代號都拒絕。"""
    if not isinstance(scope, str) or not scope:
        return [f"SCOPE_NOT_ID:{type(scope).__name__}"]
    issuers, themes = known_scopes()
    if scope in issuers or (allow_themes and scope in themes): return []
    return [f"SCOPE_UNKNOWN:{scope}"]


def _guard_ledger_row(path: str, row: dict) -> None:
    """寫入前的格式防呆：ai_field_values 的 scope、ai_extractions 的 issuer_id。"""
    target = os.path.abspath(path)
    if target == os.path.abspath(FIELD_VALUES):
        errors = scope_errors(row.get("scope"))
    elif target == os.path.abspath(EXTRACTIONS):
        errors = scope_errors(row.get("issuer_id"), allow_themes=False)   # 抽取一律是單一發行人
    else:
        return
    if errors: raise ValueError("INVALID_SCOPE:" + ";".join(errors))
    errors = as_of_errors(row)
    if errors: raise ValueError(";".join(errors))


def as_of_errors(row: dict, now=None) -> list:
    """T20：as_of 晚於 created_at（欄位值沒有 created_at 時用 available_at），或晚於現在＋5 分鐘 → FUTURE_AS_OF。"""
    value = row.get("as_of")
    if value in (None, ""): return []
    try:
        as_of = parse_time(value)
    except (TypeError, ValueError):
        return ["FUTURE_AS_OF:AS_OF_INVALID"]
    if as_of.tzinfo is None: return ["FUTURE_AS_OF:AS_OF_NO_TIMEZONE"]
    errors = []
    stamp_key = "created_at" if row.get("created_at") else "available_at" if row.get("available_at") else None
    if stamp_key and as_of > parse_time(row[stamp_key]):
        errors.append(f"FUTURE_AS_OF:as_of>{stamp_key}")
    now = now or dt.datetime.now(dt.timezone.utc)
    if as_of > now + dt.timedelta(minutes=AS_OF_CLOCK_SKEW_MINUTES):
        errors.append("FUTURE_AS_OF:as_of>now+5min")
    return errors


def scan_future_as_of(extractions: list | None = None, values: list | None = None) -> list:
    """唯讀掃描既有帳本的 as_of 違規列；只回報，不改寫。"""
    extractions = read_jsonl(EXTRACTIONS) if extractions is None else extractions
    values = read_jsonl(FIELD_VALUES) if values is None else values
    superseded = superseded_extraction_ids(extractions)
    out = []
    for ledger, rows, key in (("ai_extractions", extractions, "extraction_id"), ("ai_field_values", values, "value_id")):
        for r in rows:
            errs = as_of_errors(r, now=parse_time(r.get("created_at") or r.get("available_at")))   # 以寫入當時為準
            if errs:
                out.append({"ledger": ledger, "id": r.get(key), "field": r.get("field"),
                            "issuer_or_scope": r.get("issuer_id") or r.get("scope"), "as_of": r.get("as_of"),
                            "created_at": r.get("created_at") or r.get("available_at"), "errors": errs,
                            "superseded": r.get(key) in superseded})
    return out


def append_row(path: str, row: dict, id_field: str, require_lock: bool = True, lock_holder: str | None = None) -> dict:
    """append-only：同 ID 已存在即拒絕（不覆蓋、不改寫）；寫入前須持有專案鎖。"""
    if require_lock:
        import lock as project_lock
        scope = "docs" if os.path.basename(path).lower() == "docs_ledger.jsonl" else "ai"
        project_lock.require_holder(lock_holder, scope)
    _guard_ledger_row(path, row)
    row = dict(row)
    if any(r.get(id_field) == row.get(id_field) for r in read_jsonl(path)):
        raise ValueError(f"DUPLICATE_ID:{id_field}={row.get(id_field)}")
    row["row_sha256"] = sha256_bytes(canonical_line({k: v for k, v in row.items() if k != "row_sha256"}))
    from staging_write import append_bytes, validate_jsonl_bytes
    def validate(candidate):
        detail = validate_jsonl_bytes(candidate)
        rows = [json.loads(line) for line in candidate.decode("utf-8").splitlines() if line.strip()]
        ids = [item.get(id_field) for item in rows]
        if len(ids) != len(set(ids)):
            raise ValueError(f"DUPLICATE_ID:{id_field}={row.get(id_field)}")
        return detail
    append_bytes(path, canonical_line(row), writer="ai_fields.append_row",
                 validator=validate, lock_holder=lock_holder,
                 extra={"id_field": id_field, "id": row.get(id_field)})
    return row


def ledger_row_ok(row: dict) -> bool:
    return row.get("row_sha256") == sha256_bytes(canonical_line({k: v for k, v in row.items() if k != "row_sha256"}))


# ----------------------------------------------------------------------------
# 官方文件帳本
# ----------------------------------------------------------------------------
class _TextExtractor(HTMLParser):
    BLOCK = {"p", "div", "br", "tr", "li", "table", "h1", "h2", "h3", "h4", "h5", "h6", "section", "hr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head"): self.skip += 1
        elif tag in ("ix:header",): self.skip += 1       # iXBRL 隱藏區塊
        elif tag in self.BLOCK: self.parts.append("\n")
        elif tag in ("td", "th"): self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head", "ix:header"): self.skip = max(0, self.skip - 1)
        elif tag in self.BLOCK: self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip: self.parts.append(data)


def html_to_text(raw: bytes) -> str:
    parser = _TextExtractor()
    parser.feed(raw.decode("utf-8", errors="replace"))
    return "".join(parser.parts)


def raw_to_text(raw: bytes, filename: str) -> str:
    name = filename.lower()
    if name.endswith(".pdf"):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "in.pdf"); out = os.path.join(d, "out.txt")
            with open(src, "wb") as f: f.write(raw)
            subprocess.run(["pdftotext", "-enc", "UTF-8", src, out], check=True, capture_output=True)
            with open(out, encoding="utf-8", errors="replace") as f: return f.read()
    if name.endswith((".htm", ".html", ".xhtml", ".xml")):
        return html_to_text(raw)
    return raw.decode("utf-8", errors="replace")


def doc_id_for(raw_sha256: str) -> str:
    return "DOC_" + raw_sha256[:20]


def register_doc(issuer_id: str, doc_type: str, url: str, raw_bytes: bytes, filename: str, *, official_published_at,
                 period_end=None, robots_status="UNKNOWN", fetched_at=None, report_period_end=None, notes=None,
                 lock_holder=None) -> dict:
    """保存原文與純文字並追加一筆文件帳本。同一原文（sha256）重複註冊 → 回傳既有列。
    official_published_at 沒有官方時間戳時填 None，available_at 退回 fetched_at（保守）。"""
    if doc_type not in OFFICIAL_DOC_TYPES:
        raise ValueError(f"NOT_OFFICIAL_DOC_TYPE:{doc_type}")
    import lock as project_lock
    project_lock.require_holder(lock_holder, "docs")
    raw_sha = sha256_bytes(raw_bytes)
    doc_id = doc_id_for(raw_sha)
    existing = next((r for r in read_jsonl(DOCS_LEDGER) if r["doc_id"] == doc_id), None)
    if existing: return existing
    folder = os.path.join(RAW_DOCS, issuer_id)
    os.makedirs(folder, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", filename)
    raw_path = os.path.join(folder, f"{doc_id}_{safe}")
    text = normalize_for_store(raw_to_text(raw_bytes, filename))
    text_bytes = text.encode("utf-8")
    text_path = os.path.join(folder, f"{doc_id}.txt")
    with open(raw_path, "wb") as f: f.write(raw_bytes)
    with open(text_path, "wb") as f: f.write(text_bytes)
    fetched_at = fetched_at or now_iso()
    row = {"doc_id": doc_id, "issuer_id": issuer_id, "doc_type": doc_type, "url": url,
           "official_published_at": official_published_at, "fetched_at": fetched_at,
           "available_at": official_published_at or fetched_at, "raw_sha256": raw_sha,
           "text_sha256": sha256_bytes(text_bytes), "raw_path": os.path.relpath(raw_path, ROOT).replace("\\", "/"),
           "text_path": os.path.relpath(text_path, ROOT).replace("\\", "/"), "period_end": period_end,
           "report_period_end": report_period_end or period_end, "robots_status": robots_status, "notes": notes}
    return append_row(DOCS_LEDGER, row, "doc_id", lock_holder=lock_holder)


def normalize_for_store(text: str) -> str:
    """存檔用純文字：逐行去掉多餘空白並丟掉空行（保留行結構供人讀）。比對時再整體 normalize。"""
    lines = [re.sub(r"[ \t ]+", " ", x).strip() for x in text.replace("\r", "\n").split("\n")]
    return "\n".join(x for x in lines if x) + "\n"


def load_doc_text(doc: dict) -> str:
    path = os.path.join(ROOT, doc["text_path"])
    with open(path, "rb") as f: data = f.read()
    if sha256_bytes(data) != doc["text_sha256"]:
        raise ValueError(f"DOC_TEXT_HASH_MISMATCH:{doc['doc_id']}")
    return data.decode("utf-8")


# ----------------------------------------------------------------------------
# 有效期間與檢查清單
# ----------------------------------------------------------------------------
def effective_docs(issuer_id: str, docs: list) -> dict:
    """最近 2 份已發布財報（以 report_period_end 區分；官方發布日計）所界定的有效期間內的全部官方文件。"""
    mine = [d for d in docs if d["issuer_id"] == issuer_id and d["doc_type"] in OFFICIAL_DOC_TYPES]
    reports = {}
    for d in mine:
        if d["doc_type"] in REPORT_DOC_TYPES and d.get("report_period_end"):
            published = d.get("official_published_at") or d["available_at"]
            cur = reports.get(d["report_period_end"])
            if cur is None or parse_time(published) < parse_time(cur): reports[d["report_period_end"]] = published
    periods = sorted(reports, reverse=True)
    if len(periods) < 2:
        return {"status": "INSUFFICIENT_REPORTS", "reports": periods, "start": None, "doc_ids": [], "latest_period": None,
                "latest_report_published_at": None}
    start = reports[periods[1]]                      # 第二新財報的最早發布日
    inside = [d for d in mine if parse_time(d.get("official_published_at") or d["available_at"]) >= parse_time(start)]
    return {"status": "OK", "reports": periods[:2], "start": start, "latest_period": periods[0],
            "latest_report_published_at": reports[periods[0]],
            "doc_ids": sorted(d["doc_id"] for d in inside)}


# ----------------------------------------------------------------------------
# 抽取驗證
# ----------------------------------------------------------------------------
def number_tokens(text: str) -> set:
    """文字中的數字（去除千分位逗號與空白）。"""
    return set(re.findall(r"\d+(?:\.\d+)?", normalize(text).replace(",", "")))


def validate_extraction(ex: dict, docs: list) -> list:
    """回傳錯誤清單；空清單才可寫入抽取帳本。"""
    errors = []
    by_id = {d["doc_id"]: d for d in docs}
    for k in ("field", "issuer_id", "target_period", "doc_ids", "checklist", "quotes", "facts", "extractor_id"):
        if k not in ex: errors.append(f"MISSING_FIELD:{k}")
    if errors: return errors
    if ex["field"] not in ("C6", "C5", "I2", "I4", "I2Q"): errors.append(f"FIELD_NOT_SUPPORTED:{ex['field']}")
    if "extractor_model_family" in ex and ex["extractor_model_family"] not in EXTRACTOR_MODEL_FAMILIES:
        errors.append(f"EXTRACTOR_MODEL_FAMILY_INVALID:{ex['extractor_model_family']}")
    is_c5 = ex["field"] == "C5"
    is_i2 = ex["field"] == "I2"
    is_i4 = ex["field"] == "I4"
    is_i2q = ex["field"] == "I2Q"
    eff = effective_docs(ex["issuer_id"], docs)
    allowed = set(eff["doc_ids"])
    for doc_id in ex["doc_ids"]:
        d = by_id.get(doc_id)
        if not d: errors.append(f"DOC_NOT_IN_LEDGER:{doc_id}"); continue
        if d["issuer_id"] != ex["issuer_id"]: errors.append(f"DOC_ISSUER_MISMATCH:{doc_id}")
        if d["doc_type"] not in OFFICIAL_DOC_TYPES: errors.append(f"NOT_OFFICIAL_DOC_TYPE:{doc_id}")
        if allowed and doc_id not in allowed: errors.append(f"DOC_OUTSIDE_EFFECTIVE_PERIOD:{doc_id}")
    for item in ex["checklist"]:
        if item.get("doc_id") not in ex["doc_ids"]: errors.append(f"CHECKLIST_DOC_NOT_LISTED:{item.get('doc_id')}")
        if not isinstance(item.get("checked"), bool): errors.append("CHECKLIST_CHECKED_NOT_BOOL")
        if is_c5 and not set(item.get("sections", [])) <= C5_SECTIONS:
            errors.append(f"CHECKLIST_SECTION_INVALID:{item.get('doc_id')}")
    texts = {}
    for i, q in enumerate(ex["quotes"]):
        did = q.get("doc_id")
        if did not in ex["doc_ids"] or did not in by_id: errors.append(f"QUOTE_DOC_NOT_LISTED:{i}"); continue
        allowed_quote_types = (C5_QUOTE_TYPES if is_c5 else I2_QUOTE_TYPES if is_i2 else I4_QUOTE_TYPES if is_i4
                               else I2Q_QUOTE_TYPES if is_i2q else C6_QUOTE_TYPES)
        if q.get("type") not in allowed_quote_types:
            errors.append(f"QUOTE_TYPE_INVALID:{i}:{q.get('type')}")
        try:
            texts.setdefault(did, load_doc_text(by_id[did]))
        except (OSError, ValueError) as e:
            errors.append(f"DOC_TEXT_UNREADABLE:{did}:{e}"); continue
        if not quote_in_text(q.get("quote", ""), texts[did]):
            errors.append(f"QUOTE_NOT_IN_SOURCE:{i}")
        if q.get("type") in ("AI_NUMERIC", "SEGMENT_NUMBER", "ACQUIRED_REVENUE", "ONE_TIME_REVENUE",
                              "PREVIOUS_GUIDANCE", "LATEST_GUIDANCE", "PREVIOUS_CAPEX_GUIDANCE", "LATEST_CAPEX_GUIDANCE",
                              "COMPARABLE_CAPEX_GUIDANCE", "PREVIOUS_QUARTER_GUIDANCE", "NEXT_QUARTER_GUIDANCE",
                              "REPORTED_QUARTER_REVENUE") and not number_tokens(q.get("quote", "")):
            errors.append(f"QUOTE_HAS_NO_NUMBER:{i}")
        if q.get("type") == "SEGMENT_NUMBER" and segment_number_is_growth_only(q.get("quote", "")):
            errors.append(f"SEGMENT_NUMBER_GROWTH_ONLY:{i}")      # G-S4-C6t
        if q.get("type") == "AI_NUMERIC" and AI_MIXED_CATEGORY_RE.search(normalize(q.get("quote", ""))):
            errors.append(f"AI_NUMERIC_MIXED_CATEGORY:{i}")      # G-S4-C6q
        if q.get("type") in ("AI_NUMERIC", "SEGMENT_AI_DRIVEN", "AI_MAIN_DRIVER_NO_NUMBER", "AI_NOT_YET_REVENUE",
                             "AI_NEGLIGIBLE", "AI_MENTION_NO_PRIMACY") and not AI_TERM_RE.search(normalize(q.get("quote", ""))):
            errors.append(f"QUOTE_HAS_NO_AI_TERM:{i}")
    for lim in ex.get("limitations", []):
        if lim not in ALLOWED_LIMITATIONS: errors.append(f"LIMITATION_UNKNOWN:{lim}")
    unavailable = ex.get("call_materials_unavailable")
    if unavailable is not None:
        attempts = unavailable.get("attempts") if isinstance(unavailable, dict) else None
        if (not isinstance(unavailable, dict) or unavailable.get("code") != CALL_UNAVAILABLE_CODE or not attempts
                or not all(isinstance(a, dict) and a.get("url") and a.get("error") for a in attempts)):
            errors.append("CALL_UNAVAILABLE_ATTESTATION_INVALID")
    none_listed = ex.get("call_materials_none_listed")
    if none_listed is not None:
        ok = isinstance(none_listed, dict) and none_listed.get("code") == CALL_NONE_LISTED_CODE \
            and isinstance(none_listed.get("query"), str) and none_listed["query"].strip()
        if ok:
            try: ok = parse_time(none_listed.get("queried_at")) is not None
            except (TypeError, ValueError): ok = False
        if not ok: errors.append("CALL_NONE_LISTED_ATTESTATION_INVALID")
    for j, fact in enumerate(ex["facts"]):
        qi = fact.get("quote_index")
        if not isinstance(qi, int) or not 0 <= qi < len(ex["quotes"]): errors.append(f"FACT_QUOTE_INDEX_INVALID:{j}"); continue
        digits = number_tokens(ex["quotes"][qi].get("quote", ""))
        if is_i2q:      # G-S4-3c：財測區間寫成「中心值 ± 幅度」時，上下限由程式從原文的中心值與幅度算出，填計算結果也算在引句裡
            digits = digits | plus_minus_range_values(ex["quotes"][qi].get("quote", ""))
        value = fact.get("value")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            candidates = {str(value), (f"{value:g}" if isinstance(value, float) else str(value)), f"{abs(value):g}"}
            if not candidates & digits: errors.append(f"FACT_VALUE_NOT_IN_QUOTE:{j}")
    if is_c5:
        errors += _validate_c5_facts(ex)
    if is_i2 or is_i4:
        errors += _validate_guidance_extraction(ex)
    if is_i2q:
        errors += _validate_i2q_extraction(ex, docs)
    return errors


def _validate_i2q_extraction(ex: dict, docs: list) -> list:
    """G-S4-3c：事實名稱、數字、單位（同一幣別的金額）、引句類型對應、區間方向、前次財測的文件必須早於新財測的文件。
    三樣東西缺哪一樣不是驗證錯誤（grade_i2q 會回 null 並列出缺項）。"""
    errors, seen, currencies = [], {}, set()
    for j, fact in enumerate(ex["facts"]):
        name = fact.get("name")
        if name not in I2Q_FACTS:
            errors.append(f"I2Q_FACT_NAME_INVALID:{j}:{name}"); continue
        if name in seen: errors.append(f"I2Q_FACT_DUPLICATE:{name}")
        value = fact.get("value")
        parsed = parse_money_unit(fact.get("unit"))
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            errors.append(f"I2Q_FACT_VALUE_INVALID:{j}")
        elif parsed is not None:
            seen[name] = float(value) * parsed[1]          # 換成同一單位再比大小（原文常見「$960 million to $1.01 billion」）
        else:
            seen[name] = float(value)
        if parsed is None: errors.append(f"I2Q_FACT_UNIT_INVALID:{j}:{fact.get('unit')}")
        else: currencies.add(parsed[0])
        qi = fact.get("quote_index")
        if isinstance(qi, int) and 0 <= qi < len(ex["quotes"]) and ex["quotes"][qi].get("type") != I2Q_FACT_QUOTE[name]:
            errors.append(f"I2Q_FACT_QUOTE_TYPE_MISMATCH:{j}")
    if len(currencies) > 1: errors.append("I2Q_CURRENCY_MISMATCH")
    for lo, hi in (("previous_low", "previous_high"), ("next_low", "next_high")):
        if lo in seen and hi in seen and seen[lo] > seen[hi]: errors.append(f"I2Q_RANGE_INVALID:{lo}")
    by_id = {d["doc_id"]: d for d in docs}
    def when(q):
        d = by_id.get(q.get("doc_id"))
        return parse_time((d.get("official_published_at") or d["available_at"])) if d else None
    prev = [when(q) for q in ex["quotes"] if q.get("type") == "PREVIOUS_QUARTER_GUIDANCE"]
    nxt = [when(q) for q in ex["quotes"] if q.get("type") == "NEXT_QUARTER_GUIDANCE"]
    if prev and nxt and all(p is not None for p in prev) and all(n is not None for n in nxt) and max(prev) >= min(nxt):
        errors.append("I2Q_DOC_ORDER_INVALID")      # 前次財測必須來自比新財測更早的文件
    return errors


def parse_money_unit(unit):
    """「USD」「USD million」「TWD thousand」→ (幣別, 倍數)；無法解析回 None。"""
    parts = str(unit or "").strip().split()
    if not parts or parts[0].upper() not in CURRENCIES or len(parts) > 2:
        return None
    scale = parts[1].lower() if len(parts) == 2 else ""
    return (parts[0].upper(), UNIT_SCALES[scale]) if scale in UNIT_SCALES else None


def guidance_unit_ok(unit, field: str) -> bool:
    """I2 可比較百分比／每股或金額財測；I4 資本支出只接受金額。"""
    u = str(unit or "").strip().lower()
    if parse_money_unit(unit) is not None: return True
    return field == "I2" and (u == "percent" or re.fullmatch(r"(?:usd|twd) per share", u) is not None)


def _validate_c5_facts(ex: dict) -> list:
    errors, seen = [], set()
    for j, fact in enumerate(ex["facts"]):
        name = fact.get("name")
        if name not in C5_FACTS:
            errors.append(f"C5_FACT_NAME_INVALID:{j}:{name}"); continue
        if name in seen: errors.append(f"C5_FACT_DUPLICATE:{name}")          # 同一數字只能報一次，避免重複加總
        seen.add(name)
        value = fact.get("value")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            errors.append(f"C5_FACT_VALUE_INVALID:{j}")
        if name == C5_PCT_FACT:
            if fact.get("unit") != "%": errors.append(f"C5_FACT_UNIT_INVALID:{j}:{fact.get('unit')}")
            if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 100:
                errors.append(f"C5_FACT_VALUE_INVALID:{j}")
        elif parse_money_unit(fact.get("unit")) is None:
            errors.append(f"C5_FACT_UNIT_INVALID:{j}:{fact.get('unit')}")
        qi = fact.get("quote_index")
        if isinstance(qi, int) and 0 <= qi < len(ex["quotes"]) and ex["quotes"][qi].get("type") != C5_FACTS[name]:
            errors.append(f"C5_FACT_QUOTE_TYPE_MISMATCH:{j}")
    if {"acquired_revenue", C5_PCT_FACT} <= seen:
        errors.append("C5_FACT_CONFLICT:acquired_revenue+acquired_revenue_pct_of_prior_year")   # 同一筆併購營收不可報兩次
    return errors


def _validate_guidance_extraction(ex: dict) -> list:
    """I2／I4 只保存前後兩次引句與數字；方向由程式計算。"""
    errors, seen = [], set()
    for j, fact in enumerate(ex["facts"]):
        name = fact.get("name")
        if name not in GUIDANCE_FACTS:
            errors.append(f"{ex['field']}_FACT_NAME_INVALID:{j}:{name}"); continue
        if name in seen: errors.append(f"{ex['field']}_FACT_DUPLICATE:{name}")
        seen.add(name)
        value = fact.get("value")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            errors.append(f"{ex['field']}_FACT_VALUE_INVALID:{j}")
        if not guidance_unit_ok(fact.get("unit"), ex["field"]):
            errors.append(f"{ex['field']}_FACT_UNIT_INVALID:{j}:{fact.get('unit')}")
        qi = fact.get("quote_index")
        if isinstance(qi, int) and 0 <= qi < len(ex["quotes"]):
            expected = ({"PREVIOUS_GUIDANCE"} if ex["field"] == "I2" else {"PREVIOUS_CAPEX_GUIDANCE", "COMPARABLE_CAPEX_GUIDANCE"}) \
                if name.startswith("previous_") else (
                {"LATEST_GUIDANCE"} if ex["field"] == "I2" else {"LATEST_CAPEX_GUIDANCE", "COMPARABLE_CAPEX_GUIDANCE"})
            if ex["quotes"][qi].get("type") not in expected:
                errors.append(f"{ex['field']}_FACT_QUOTE_TYPE_MISMATCH:{j}")
    explicit = [q for q in ex["quotes"] if q.get("type") in {"EXPLICIT_UP", "EXPLICIT_MAINTAIN", "EXPLICIT_DOWN"}]
    types = {q.get("type") for q in ex["quotes"]}
    if ex["field"] == "I2":
        # G-S4-3a：沒有完整四個數字時，可改用文字型明確修正（此時不得混入部分數字）
        if seen != GUIDANCE_FACTS and not (not seen and "GUIDANCE_REVISION_TEXT" in types):
            errors.append("I2_FACT_SET_INCOMPLETE")
        if not ex.get("as_of"):
            errors.append("I2_AS_OF_REQUIRED")
        else:
            try: parse_time(ex["as_of"])
            except (TypeError, ValueError): errors.append("I2_AS_OF_INVALID")
    else:
        numeric_complete = seen == GUIDANCE_FACTS
        statements = {q.get("type") for q in ex["quotes"]} >= {"PREVIOUS_CAPEX_STATEMENT", "LATEST_CAPEX_STATEMENT"}
        noncomparable = not seen and statements and ex.get("null_reason") == "NO_COMPARABLE_GUIDANCE"
        definition_change = "DEFINITION_CHANGE" in types
        if types & ({"COMPARABLE_CAPEX_GUIDANCE"} | set(COMPARABLE_EXPLICIT)) and not definition_change:
            errors.append("I4_COMPARABLE_WITHOUT_DEFINITION_CHANGE")
        if "COMPARABLE_CAPEX_GUIDANCE" in types and types & set(COMPARABLE_EXPLICIT):
            errors.append("I4_COMPARABLE_NUMERIC_AND_EXPLICIT_BOTH_PRESENT")
        if not numeric_complete and not (not seen and len(explicit) == 1) and not noncomparable and not definition_change:
            errors.append("I4_EVIDENCE_INCOMPLETE")
        if numeric_complete and (explicit or statements):
            errors.append("I4_NUMERIC_AND_EXPLICIT_BOTH_PRESENT")
    return errors


# ----------------------------------------------------------------------------
# C6 判級
# ----------------------------------------------------------------------------
def issuer_market(issuer_id: str, registry=None) -> str | None:
    """發行人的上市地：任一證券在台股即視為 TW（從嚴，需查 MOPS 法說會）；否則 US。"""
    if registry is None:
        path = os.path.join(SPINE_DIR, "entity_registry.json")
        if not os.path.exists(path): return None
        with open(path, encoding="utf-8") as f: registry = json.load(f)
    markets = {s.get("market") for s in registry.get("securities", []) if s.get("issuer_id") == issuer_id}
    if "TW" in markets: return "TW"
    return "US" if markets else None


def grade_c6(ex: dict, docs: list, market: str | None = None, reviews: list | None = None) -> dict:
    """依 C6 判定表 v0.3 取第一個符合者（A → B → D1/D2 → C）。AI 抽取只提供引句；等級在這裡算。
    回傳 {grade: A|B|C|D1|D2|None, value: A|B|C|D|None, flags[], reason, evidence[], ignored_mentions[]}。"""
    errors = validate_extraction(ex, docs)
    if errors:
        return {"grade": None, "value": None, "flags": [], "reason": "EXTRACTION_INVALID", "errors": errors, "evidence": []}
    by_id = {d["doc_id"]: d for d in docs}
    eff = effective_docs(ex["issuer_id"], docs)
    if eff["status"] != "OK":
        return {"grade": None, "value": None, "flags": [], "reason": "EFFECTIVE_PERIOD_UNDEFINED:" + eff["status"], "evidence": []}
    quotes = [dict(q, index=i, type=effective_type(q)) for i, q in enumerate(ex["quotes"])]
    by_type = {}
    for q in quotes: by_type.setdefault(q["type"], []).append(q)
    ev = lambda *qs: [{"index": q["index"], "type": q["type"], "doc_id": q["doc_id"], "url": by_id[q["doc_id"]]["url"],
                       "quote": q["quote"]} for q in qs]
    ignored = [{"index": q["index"], "doc_id": q["doc_id"], "declared_type": ex["quotes"][q["index"]]["type"],
                "reason": "AI_MENTION_NO_PRIMACY"} for q in by_type.get("AI_MENTION_NO_PRIMACY", [])]
    limitations = sorted(set(ex.get("limitations", [])))
    out = lambda grade, value, flags, reason, evidence: {"grade": grade, "value": value, "flags": flags, "reason": reason,
                                                       "evidence": evidence, "ignored_mentions": ignored,
                                                       "limitations": limitations}
    if by_type.get("AI_NUMERIC"):
        return out("A", "A", [], "AI_NUMERIC", ev(*by_type["AI_NUMERIC"][:2]))
    seg_n, seg_ai = by_type.get("SEGMENT_NUMBER"), by_type.get("SEGMENT_AI_DRIVEN")
    if seg_n and seg_ai:
        # v0.3.1：SEGMENT_PROXY 類 A 需有人工複核（ai_reviews.jsonl，綁定這兩句引句的雜湊）才計入；未複核前視為 B。
        pair = sorted([quote_hash(seg_n[0]["quote"]), quote_hash(seg_ai[0]["quote"])])
        reviewed = any(r.get("flag") == REVIEW_FLAG_SEGMENT_PROXY and r.get("issuer_id") == ex["issuer_id"]
                       and r.get("field") == ex["field"] and sorted(r.get("quote_sha256s", [])) == pair for r in (reviews or []))
        if reviewed:
            return out("A", "A", ["SEGMENT_PROXY", REVIEW_FLAG_SEGMENT_PROXY], "SEGMENT_NUMBER+SEGMENT_AI_DRIVEN",
                       ev(seg_n[0], seg_ai[0]))
        failed = any(r.get("flag") == REVIEW_FLAG_SEGMENT_PROXY_FAILED and r.get("issuer_id") == ex["issuer_id"]
                     and r.get("field") == ex["field"] and sorted(r.get("quote_sha256s", [])) == pair for r in (reviews or []))
        if failed:      # T30g：複核不通過 → 仍判 B（不計 A）
            return out("B", "B", ["SEGMENT_PROXY", REVIEW_FLAG_SEGMENT_PROXY_FAILED], "SEGMENT_PROXY_REVIEW_FAILED", ev(seg_n[0], seg_ai[0]))
        return out("B", "B", ["SEGMENT_PROXY", "SEGMENT_PROXY_UNREVIEWED"], "SEGMENT_PROXY_AWAITING_REVIEW", ev(seg_n[0], seg_ai[0]))
    if seg_n and not seg_ai:
        # v0.3.2：只有部門數字、沒有合格的部門 AI 句，不是 AI 營收歸因 → 不判 B，記入 ignored_mentions 後繼續往下判（B → D → C）
        ignored.extend({"index": q["index"], "doc_id": q["doc_id"], "declared_type": ex["quotes"][q["index"]]["type"],
                        "reason": "SEGMENT_NUMBER_WITHOUT_AI_LINK"} for q in seg_n)
    if by_type.get("AI_MAIN_DRIVER_NO_NUMBER"):
        return out("B", "B", [], "AI_MAIN_DRIVER_NO_NUMBER", ev(*by_type["AI_MAIN_DRIVER_NO_NUMBER"][:2]))
    # v0.3 修改 1：先檢查 D1／D2，再檢查 C。D 須有「最新一份財報之後」的官方引句。
    for dtype, grade in (("AI_NOT_YET_REVENUE", "D1"), ("AI_NEGLIGIBLE", "D2")):
        for q in by_type.get(dtype, []):
            d = by_id[q["doc_id"]]
            published = parse_time(d.get("official_published_at") or d["available_at"])
            if published >= parse_time(eff["latest_report_published_at"]):
                return out(grade, "D", [], dtype, ev(q))
    # C：檢查清單須完成。法說資料（v0.3 修改 3）：台股必須有 MOPS 法人說明會簡報；美股自動化拿不到時，
    # 需有 CALL_MATERIALS_UNAVAILABLE_AUTOMATED 紀錄（含嘗試的網址與錯誤），可判 C 但標 C_WITHOUT_CALL_MATERIALS。
    market = market or issuer_market(ex["issuer_id"])
    flags = []
    if not any(by_id[i]["doc_type"] in CALL_DOC_TYPES for i in eff["doc_ids"]):
        if market == "TW" and not ex.get("call_materials_none_listed"):
            return out(None, None, [], "TW_CALL_MATERIALS_REQUIRED", [])
        if market == "US" and ex.get("call_materials_unavailable"):
            flags = ["C_WITHOUT_CALL_MATERIALS"]
        elif market == "TW" and ex.get("call_materials_none_listed"):      # G-S4-C6s：已查證沒有法說會場次
            flags = ["C_WITHOUT_CALL_MATERIALS"]
        else:
            return out(None, None, [], "CALL_MATERIALS_MISSING", [])
    done = {c["doc_id"] for c in ex["checklist"] if c.get("checked") is True}
    missing = sorted(set(eff["doc_ids"]) - done)
    if missing:
        res = out(None, None, [], "CHECKLIST_INCOMPLETE", [])
        res["missing_doc_ids"] = missing
        return res
    return out("C", "C", flags, "CHECKLIST_COMPLETE_NO_AI_ATTRIBUTION", [])


def c1_revenue_pair(issuer_id: str, observations: list, cutoff: str, mops_enabled: bool = True) -> dict | None:
    """與 trend.py 的 C1 相同的選列方式：最新一季營收與去年同季營收（時點重播、合併報表範圍一致）。"""
    import health
    import trend
    issuer = [r for r in observations if r.get("issuer_id") == issuer_id]
    issuer, _ = health.gate_pending_sources(issuer, mops_enabled, False)
    rows = health.consistent_report_scope(health.point_in_time(issuer, issuer_id, cutoff))
    rev = {r["period_end"]: r for r in health._latest_by_period(rows, "FIN_REVENUE", "Q")}
    if not rev:
        return None
    end = max(rev)
    ly = trend._same_quarter_last_year(rev, end)
    if not ly:
        return None
    cur, prior = rev[end], rev[ly]
    return {"period_end": end, "prior_period_end": ly, "current": float(cur["value"]), "prior": float(prior["value"]),
            "currency": cur.get("unit"), "observation_ids": sorted({cur["observation_id"], prior["observation_id"]})}


def _c5_checklist(ex: dict, docs: list, market: str | None = None) -> dict:
    """最新一份財報（target_period 那一期的 10-Q／10-K／MOPS 財報）的指定章節都查過才算完成：
    美股三處；台股兩處（G-S4-2b：季報沒有營運概況）。"""
    required = C5_SECTIONS_TW if (market or issuer_market(ex["issuer_id"])) == "TW" else C5_SECTIONS
    by_id = {d["doc_id"]: d for d in docs}
    filings = [i for i in ex["doc_ids"] if by_id.get(i, {}).get("doc_type") in C5_FILING_TYPES
               and by_id[i].get("report_period_end") == ex["target_period"]]
    if not filings:
        return {"complete": False, "reason": "LATEST_FILING_NOT_IN_LEDGER", "filing_doc_ids": []}
    done = [c for c in ex["checklist"] if c.get("doc_id") in filings and c.get("checked") is True
            and set(c.get("sections", [])) >= required]
    missing = sorted(required - set().union(*[set(c.get("sections", [])) for c in ex["checklist"]
                                                  if c.get("doc_id") in filings and c.get("checked") is True] or [set()]))
    return {"complete": bool(done), "reason": None if done else "CHECKLIST_INCOMPLETE", "filing_doc_ids": filings,
            "missing_sections": missing, "required_sections": sorted(required)}


def c5_condition(delta: float, x: float, prior: float) -> bool:
    """G-S4-1／G-S4-2：成長時 X ≥ 0.5 × ΔRev；衰退或持平時 X ≥ 去年同季營收 × 2%。"""
    return (delta > 0 and x >= C5_MAJORITY * delta) or (delta <= 0 and x >= C5_DECLINE_MATERIALITY * prior)


def grade_c5(ex: dict, docs: list, revenue: dict | None, market: str | None = None) -> dict:
    """C5「成長主要來自併購或一次性」。回傳 {value: 有|無|None, reason, flags[], calc{}, evidence[]}。
    ΔRev 取自 C1 的營收資料（revenue，見 c1_revenue_pair），不由 AI 提供；X＝併購營收＋一次性營收。"""
    base = {"value": None, "flags": [], "calc": None, "evidence": []}
    errors = validate_extraction(ex, docs)
    if errors:
        return dict(base, reason="EXTRACTION_INVALID", errors=errors)
    if ex["field"] != "C5":
        return dict(base, reason="NOT_C5")
    if not revenue or revenue.get("current") is None or revenue.get("prior") is None:
        return dict(base, reason="C1_REVENUE_UNAVAILABLE")
    if revenue.get("period_end") != ex["target_period"]:
        return dict(base, reason="REVENUE_PERIOD_MISMATCH", calc={"revenue_period": revenue.get("period_end"),
                                                                 "target_period": ex["target_period"]})
    if revenue["prior"] <= 0:
        return dict(base, reason="PRIOR_REVENUE_NOT_POSITIVE")
    by_id = {d["doc_id"]: d for d in docs}
    amounts, evidence = {}, []
    pct = None
    for fact in ex["facts"]:
        if fact["name"] == C5_PCT_FACT:                                   # G-S4-2a：百分比 × C1 同源的去年同季營收
            pct = float(fact["value"])
            amounts["acquired_revenue"] = pct / 100.0 * revenue["prior"]
            q = ex["quotes"][fact["quote_index"]]
            evidence.append({"fact": fact["name"], "value": fact["value"], "unit": "%", "doc_id": q["doc_id"],
                             "url": by_id[q["doc_id"]]["url"], "quote": q["quote"]})
            continue
        currency, scale = parse_money_unit(fact["unit"])
        if currency != revenue.get("currency"):
            return dict(base, reason="CURRENCY_MISMATCH", calc={"fact_currency": currency, "revenue_currency": revenue.get("currency")})
        amounts[fact["name"]] = float(fact["value"]) * scale
        q = ex["quotes"][fact["quote_index"]]
        evidence.append({"fact": fact["name"], "value": fact["value"], "unit": fact["unit"], "doc_id": q["doc_id"],
                         "url": by_id[q["doc_id"]]["url"], "quote": q["quote"]})
    for q in ex["quotes"]:
        if q["type"] == "NO_MATERIAL_ACQUISITION":
            evidence.append({"fact": "no_material_acquisition", "doc_id": q["doc_id"], "url": by_id[q["doc_id"]]["url"], "quote": q["quote"]})
    checklist = _c5_checklist(ex, docs, market)
    delta = revenue["current"] - revenue["prior"]
    disclosed = sum(amounts.values()) if amounts else None
    calc = {"currency": revenue["currency"], "revenue_current": revenue["current"], "revenue_prior": revenue["prior"],
            "revenue_period_end": revenue["period_end"], "revenue_prior_period_end": revenue.get("prior_period_end"),
            "revenue_observation_ids": revenue.get("observation_ids", []), "delta_rev": delta,
            "acquired_revenue": amounts.get("acquired_revenue"), "one_time_revenue": amounts.get("one_time_revenue"),
            "acquired_revenue_pct_of_prior_year": pct,
            "checklist_complete": checklist["complete"], "checklist_filing_doc_ids": checklist["filing_doc_ids"],
            "required_sections": checklist.get("required_sections"),
            "rule": "ΔRev>0：X≥0.5×ΔRev；ΔRev≤0：X≥0.02×去年同季營收"}
    if checklist["complete"]:
        x = disclosed if disclosed is not None else 0.0                    # 清單完成且沒有揭露 → 0
        threshold = C5_MAJORITY * delta if delta > 0 else C5_DECLINE_MATERIALITY * revenue["prior"]
        value = "有" if c5_condition(delta, x, revenue["prior"]) else "無"
        calc.update(x=x, threshold=threshold, formula=f"ΔRev={delta:,.0f}；X={x:,.0f}；門檻={threshold:,.0f}")
        return {"value": value, "reason": "COMPUTED", "flags": [] if disclosed is not None else ["X_ZERO_NO_DISCLOSURE"],
                "calc": calc, "evidence": evidence}
    # G-S4-1a（Claude 2026-10-16 同意）：清單未完成時，沒揭露就是 null；已揭露的數字本身就足以判「有」時
    # （X 只增不減，「有」是保守方向），判「有」並標 X_LOWER_BOUND_CHECKLIST_INCOMPLETE。
    if disclosed is not None and c5_condition(delta, disclosed, revenue["prior"]):
        threshold = C5_MAJORITY * delta if delta > 0 else C5_DECLINE_MATERIALITY * revenue["prior"]
        calc.update(x=disclosed, threshold=threshold, formula=f"ΔRev={delta:,.0f}；X≥{disclosed:,.0f}；門檻={threshold:,.0f}")
        return {"value": "有", "reason": "COMPUTED_LOWER_BOUND", "flags": ["X_LOWER_BOUND_CHECKLIST_INCOMPLETE"],
                "calc": calc, "evidence": evidence}
    calc.update(x=None, missing_sections=checklist.get("missing_sections"))
    return {"value": None, "reason": checklist["reason"] or "CHECKLIST_INCOMPLETE", "flags": [], "calc": calc, "evidence": evidence}


def _guidance_midpoints(ex: dict) -> dict:
    facts = {f["name"]: f for f in ex["facts"]}
    units = {f.get("unit") for f in facts.values()}
    if len(units) != 1:
        return {"error": "GUIDANCE_UNIT_MISMATCH"}
    previous = (float(facts["previous_low"]["value"]) + float(facts["previous_high"]["value"])) / 2
    latest = (float(facts["latest_low"]["value"]) + float(facts["latest_high"]["value"])) / 2
    if previous <= 0:
        return {"error": "PREVIOUS_MIDPOINT_NOT_POSITIVE"}
    return {"previous": previous, "latest": latest, "change": latest / previous - 1, "unit": units.pop()}


def _guidance_evidence(ex: dict, docs: list) -> list:
    by_id = {d["doc_id"]: d for d in docs}
    return [{"doc_id": q["doc_id"], "url": by_id[q["doc_id"]]["url"], "type": q["type"], "quote": q["quote"]}
            for q in ex["quotes"]]


def revision_text_direction(quote: str, target_period) -> dict:
    """G-S4-3a：單一引句的修正方向（字詞表比對）與是否寫出同一目標期間。"""
    q = normalize(quote)
    years = re.findall(r"(?:19|20)\d{2}", str(target_period or ""))
    period_ok = any(y in q or re.search(r"\bFY\s?'?" + y[2:] + r"\b", q, re.I) for y in years)
    up, down = bool(REVISION_UP_RE.search(q)), bool(REVISION_DOWN_RE.search(q))
    direction = "上修" if up and not down else "下修" if down and not up else None
    return {"period_ok": bool(period_ok), "up": up, "down": down, "direction": direction}


def _grade_i2_revision_text(ex: dict, docs: list, calc: dict) -> dict:
    checks = [revision_text_direction(q["quote"], ex["target_period"]) for q in ex["quotes"] if q["type"] == "GUIDANCE_REVISION_TEXT"]
    calc = dict(calc, method="TEXT_REVISION", revisions=checks)
    if not all(c["period_ok"] and c["direction"] for c in checks):
        return {"value": None, "reason": "REVISION_TEXT_NOT_EXPLICIT", "calc": calc, "evidence": _guidance_evidence(ex, docs)}
    if len({c["direction"] for c in checks}) != 1:
        return {"value": None, "reason": "REVISION_TEXT_CONFLICT", "calc": calc, "evidence": _guidance_evidence(ex, docs)}
    return {"value": checks[0]["direction"], "reason": "COMPUTED_TEXT_REVISION", "calc": calc, "evidence": _guidance_evidence(ex, docs)}


def grade_i2(ex: dict, docs: list) -> dict:
    """G-S4-3～4：同一目標期間財測中點變動；最新引句超過 120 天即失效。G-S4-3a：沒有四個數字時改用文字型明確修正。"""
    base = {"value": None, "reason": None, "calc": None, "evidence": []}
    errors = validate_extraction(ex, docs)
    if errors: return dict(base, reason="EXTRACTION_INVALID", errors=errors)
    if ex["field"] != "I2": return dict(base, reason="NOT_I2")
    by_id = {d["doc_id"]: d for d in docs}
    latest_doc_ids = {q["doc_id"] for q in ex["quotes"] if q["type"] in ("LATEST_GUIDANCE", "GUIDANCE_REVISION_TEXT")}
    latest_times = [parse_time(by_id[i].get("official_published_at") or by_id[i]["available_at"]) for i in latest_doc_ids]
    as_of = parse_time(ex["as_of"])
    if not latest_times or as_of < max(latest_times):
        return dict(base, reason="LATEST_GUIDANCE_TIME_INVALID")
    age_days = (as_of - max(latest_times)).total_seconds() / 86400
    if age_days > I2_FRESH_DAYS:
        return dict(base, reason="LATEST_GUIDANCE_STALE", calc={"age_days": age_days, "fresh_days": I2_FRESH_DAYS})
    if not ex["facts"]:
        return _grade_i2_revision_text(ex, docs, {"age_days": age_days})
    calc = _guidance_midpoints(ex)
    if calc.get("error"): return dict(base, reason=calc["error"], calc=calc)
    change = calc["change"]
    value = "上修" if change >= I2_MOVE_THRESHOLD else "下修" if change <= -I2_MOVE_THRESHOLD else "維持"
    calc.update(threshold=I2_MOVE_THRESHOLD, age_days=age_days)
    return {"value": value, "reason": "COMPUTED", "calc": calc, "evidence": _guidance_evidence(ex, docs)}


def grade_i2q(ex: dict, docs: list, revenue: dict | None = None) -> dict:
    """G-S4-3c：上修＝實際營收 ≥ 前次財測上緣 且 新財測中點 > 實際營收；下修＝實際營收 < 前次財測下緣 或 新財測中點 < 實際營收 × (1−3%)；
    其餘維持；三樣東西缺任何一樣 → null。實際營收優先用帳本同源的 FIN_REVENUE（revenue：c1_revenue_pair 的結果，期間與幣別要對得上），
    找不到才用引句的 reported_revenue。區間只有單點時，上下緣相同。"""
    base = {"value": None, "reason": None, "calc": None, "evidence": [], "flags": []}
    errors = validate_extraction(ex, docs)
    if errors: return dict(base, reason="EXTRACTION_INVALID", errors=errors)
    if ex["field"] != "I2Q": return dict(base, reason="NOT_I2Q")
    facts = {f["name"]: f for f in ex["facts"]}
    cur = next((parse_money_unit(f["unit"])[0] for f in ex["facts"]), None)
    scale = lambda f: parse_money_unit(f["unit"])[1]
    missing = [n for n in ("previous_low", "previous_high", "next_low", "next_high") if n not in facts]
    actual, source, quote_actual = None, None, None
    if "reported_revenue" in facts: quote_actual = float(facts["reported_revenue"]["value"]) * scale(facts["reported_revenue"])
    if revenue and revenue.get("current") is not None and revenue.get("currency") == cur and revenue.get("period_end") == ex["target_period"]:
        actual, source = float(revenue["current"]), "LEDGER_FIN_REVENUE"
    elif quote_actual is not None:
        actual, source = quote_actual, "QUOTE"
    else:
        missing.append("actual_revenue")
    if missing:
        return dict(base, reason="I2Q_MISSING_ITEM", calc={"missing": missing, "currency": cur}, evidence=_guidance_evidence(ex, docs))
    p_low, p_high = float(facts["previous_low"]["value"]) * scale(facts["previous_low"]), float(facts["previous_high"]["value"]) * scale(facts["previous_high"])
    n_low, n_high = float(facts["next_low"]["value"]) * scale(facts["next_low"]), float(facts["next_high"]["value"]) * scale(facts["next_high"])
    n_mid = (n_low + n_high) / 2
    flags = []
    if source == "LEDGER_FIN_REVENUE" and quote_actual is not None and actual > 0 \
            and abs(quote_actual - actual) / actual > I2Q_ACTUAL_MISMATCH_TOLERANCE:
        flags.append("ACTUAL_SOURCE_DIFFERS")
    if actual >= p_high and n_mid > actual: value = "上修"
    elif actual < p_low or n_mid < actual * (1 - I2Q_DOWN_TOLERANCE): value = "下修"
    else: value = "維持"
    calc = {"currency": cur, "actual_revenue": actual, "actual_source": source, "actual_quote_value": quote_actual,
            "previous_low": p_low, "previous_high": p_high, "next_low": n_low, "next_high": n_high, "next_midpoint": n_mid,
            "down_tolerance": I2Q_DOWN_TOLERANCE, "beat_upper": actual >= p_high, "below_lower": actual < p_low,
            "next_vs_actual": n_mid / actual - 1 if actual else None,
            "revenue_observation_ids": (revenue or {}).get("observation_ids", []) if source == "LEDGER_FIN_REVENUE" else []}
    return {"value": value, "reason": "COMPUTED", "calc": calc, "evidence": _guidance_evidence(ex, docs), "flags": flags}


def grade_i4(ex: dict, docs: list) -> dict:
    """G-S4-5：四家雲端業者資本支出說法；數字中點 ±3%，或官方明示比較文字。"""
    base = {"value": None, "reason": None, "calc": None, "evidence": []}
    errors = validate_extraction(ex, docs)
    if errors: return dict(base, reason="EXTRACTION_INVALID", errors=errors)
    if ex["field"] != "I4": return dict(base, reason="NOT_I4")
    if any(q["type"] == "DEFINITION_CHANGE" for q in ex["quotes"]):
        # G-S4-5a：定義改變 → 不可比，等同 null；例外是公司另給同一定義的可比數字（至少一側的數字引自 COMPARABLE_CAPEX_GUIDANCE）
        comparable = len(ex["facts"]) == len(GUIDANCE_FACTS) and any(
            ex["quotes"][f["quote_index"]]["type"] == "COMPARABLE_CAPEX_GUIDANCE" for f in ex["facts"])
        stated = [q for q in ex["quotes"] if q["type"] in COMPARABLE_EXPLICIT]
        if not comparable and stated:
            # G-S4-5b：同一定義下的文字說法；每一句都必須有排除字詞，方向必須一致
            excluded = [bool(DEFINITION_EXCLUSION_RE.search(normalize(q["quote"]))) for q in stated]
            directions = {COMPARABLE_EXPLICIT[q["type"]] for q in stated}
            calc = {"method": "COMPARABLE_TEXT_EXPLICIT", "exclusion_found": excluded}
            if all(excluded) and len(directions) == 1:
                return {"value": directions.pop(), "reason": "COMPARABLE_EXPLICIT_COMPARISON", "calc": calc,
                        "evidence": _guidance_evidence(ex, docs)}
            reason = "NOT_COMPARABLE_DEFINITION_CHANGE" if not all(excluded) else "COMPARABLE_EXPLICIT_CONFLICT"
            return {"value": None, "reason": reason, "calc": calc, "evidence": _guidance_evidence(ex, docs)}
        if not comparable:
            return {"value": None, "reason": "NOT_COMPARABLE_DEFINITION_CHANGE", "calc": {"method": "DEFINITION_CHANGE"},
                    "evidence": _guidance_evidence(ex, docs)}
        calc = _guidance_midpoints(ex)
        if calc.get("error"): return dict(base, reason=calc["error"], calc=calc)
        change = calc["change"]
        value = "上修" if change > I4_MOVE_THRESHOLD else "下修" if change < -I4_MOVE_THRESHOLD else "維持"
        calc.update(threshold=I4_MOVE_THRESHOLD, method="COMPARABLE_SAME_DEFINITION")
        return {"value": value, "reason": "COMPUTED", "calc": calc, "evidence": _guidance_evidence(ex, docs)}
    if ex.get("null_reason") == "NO_COMPARABLE_GUIDANCE":
        return {"value": None, "reason": "NO_COMPARABLE_GUIDANCE", "calc": {"method": "NONCOMPARABLE_STATEMENTS"},
                "evidence": _guidance_evidence(ex, docs)}
    explicit = [q["type"] for q in ex["quotes"] if q["type"].startswith("EXPLICIT_")]
    if explicit:
        value = {"EXPLICIT_UP": "上修", "EXPLICIT_MAINTAIN": "維持", "EXPLICIT_DOWN": "下修"}[explicit[0]]
        return {"value": value, "reason": "EXPLICIT_COMPARISON", "calc": {"method": "TEXT_EXPLICIT"},
                "evidence": _guidance_evidence(ex, docs)}
    calc = _guidance_midpoints(ex)
    if calc.get("error"): return dict(base, reason=calc["error"], calc=calc)
    change = calc["change"]
    value = "上修" if change > I4_MOVE_THRESHOLD else "下修" if change < -I4_MOVE_THRESHOLD else "維持"
    calc.update(threshold=I4_MOVE_THRESHOLD, method="NUMERIC_MIDPOINT")
    return {"value": value, "reason": "COMPUTED", "calc": calc, "evidence": _guidance_evidence(ex, docs)}


def aggregate_i2(company_results: dict) -> dict:
    """G-S4-4：可比較公司 #上修 − #下修；0 家時回 null。"""
    comparable = {k: v for k, v in company_results.items() if v and v.get("value") in {"上修", "維持", "下修"}}
    if not comparable:
        return {"value": None, "reason": "NO_COMPARABLE_COMPANIES", "n_comparable": 0, "companies": {}}
    counts = {x: sum(v["value"] == x for v in comparable.values()) for x in ("上修", "維持", "下修")}
    return {"value": counts["上修"] - counts["下修"], "reason": "COMPUTED", "n_comparable": len(comparable),
            "counts": counts, "companies": {k: v["value"] for k, v in sorted(comparable.items())}}


def aggregate_i2q(company_results: dict) -> dict:
    """G-S4-3c：與 I2 相同的計法（上修家數 − 下修家數；0 家可比較 → null），只是來源是下一季財測。影子並列。"""
    return dict(aggregate_i2(company_results), field="I2Q")


def aggregate_i2_combined(i2_results: dict, i2q_results: dict) -> dict:
    """I2 和 I2Q 合併：每家公司優先用年度財測的 I2（可比較時），否則用 I2Q。回傳附每家用哪個來源。"""
    merged, source = {}, {}
    for k in sorted(set(i2_results) | set(i2q_results)):
        a, b = i2_results.get(k), i2q_results.get(k)
        if a and a.get("value") in {"上修", "維持", "下修"}: merged[k], source[k] = a, "I2"
        elif b and b.get("value") in {"上修", "維持", "下修"}: merged[k], source[k] = b, "I2Q"
    return dict(aggregate_i2(merged), field="I2+I2Q", sources=source)


def aggregate_i4(company_results: dict) -> dict:
    """G-S4-6：至少三家；多家下修／一家下修／上修／持平。"""
    comparable = {k: v for k, v in company_results.items() if v and v.get("value") in {"上修", "維持", "下修"}}
    if len(comparable) < 3:
        return {"value": None, "score": None, "reason": "FEWER_THAN_THREE_COMPANIES",
                "n_comparable": len(comparable), "companies": {k: v["value"] for k, v in sorted(comparable.items())}}
    counts = {x: sum(v["value"] == x for v in comparable.values()) for x in ("上修", "維持", "下修")}
    if counts["下修"] >= 2: value, score = "多家下修", -1
    elif counts["下修"] == 1: value, score = "一家下修", 0
    elif counts["上修"] >= 1: value, score = "上修", 1
    else: value, score = "持平", 0
    return {"value": value, "score": score, "reason": "COMPUTED", "n_comparable": len(comparable),
            "counts": counts, "companies": {k: v["value"] for k, v in sorted(comparable.items())}}


def _compare_dual_guidance(ex1: dict, ex2: dict, docs: list) -> dict:
    rubric, accepted = (RUBRIC_I2, I2_EXTRACTION_RUBRICS) if ex1["field"] == "I2" else (RUBRIC_I4, I4_EXTRACTION_RUBRICS)
    for e in (ex1, ex2):
        if e.get("rubric_version", rubric) not in accepted:
            return {"value": None, "status": STATUS_INSUFFICIENT, "problems": ["RUBRIC_VERSION_MISMATCH"], "detail": None}
    g1 = grade_i2(ex1, docs) if ex1["field"] == "I2" else grade_i4(ex1, docs)
    g2 = grade_i2(ex2, docs) if ex2["field"] == "I2" else grade_i4(ex2, docs)
    detail = {"first": g1, "second": g2}
    if g1["value"] is None or g2["value"] is None:
        return {"value": None, "status": STATUS_INSUFFICIENT, "problems": [],
                "detail": dict(detail, flags=["REVIEW_DISAGREEMENT"] if g1["value"] != g2["value"] else [])}
    if g1["value"] != g2["value"]:
        return {"value": None, "status": STATUS_DISAGREE, "problems": [],
                "detail": dict(detail, flags=["REVIEW_DISAGREEMENT"], reason="NUMERIC_OR_DIRECTION_DISAGREEMENT")}
    return {"value": g1["value"], "status": STATUS_VERIFIED, "problems": [], "detail": dict(detail, flags=[])}


def _compare_dual_i2q(ex1: dict, ex2: dict, docs: list, revenue: dict | None) -> dict:
    for e in (ex1, ex2):
        if e.get("rubric_version", RUBRIC_I2Q) != RUBRIC_I2Q:
            return {"value": None, "status": STATUS_INSUFFICIENT, "problems": ["RUBRIC_VERSION_MISMATCH"], "detail": None}
    g1, g2 = grade_i2q(ex1, docs, revenue), grade_i2q(ex2, docs, revenue)
    detail = {"first": g1, "second": g2}
    if g1["value"] is None or g2["value"] is None:
        return {"value": None, "status": STATUS_INSUFFICIENT, "problems": [],
                "detail": dict(detail, flags=["REVIEW_DISAGREEMENT"] if g1["value"] != g2["value"] else [])}
    if g1["value"] != g2["value"]:
        return {"value": None, "status": STATUS_DISAGREE, "problems": [],
                "detail": dict(detail, flags=["REVIEW_DISAGREEMENT"], reason="NUMERIC_OR_DIRECTION_DISAGREEMENT")}
    return {"value": g1["value"], "status": STATUS_VERIFIED, "problems": [],
            "detail": dict(detail, flags=sorted(set(g1["flags"]) | set(g2["flags"])))}


def _compare_dual_c5(ex1: dict, ex2: dict, docs: list, revenue: dict | None) -> dict:
    """C5 是等級型欄位：不一致取較保守（分數較低＝「有」）並標 REVIEW_DISAGREEMENT；任一為 null → null。"""
    for e in (ex1, ex2):
        if e.get("rubric_version", RUBRIC_C5) not in C5_EXTRACTION_RUBRICS:
            return {"value": None, "status": STATUS_INSUFFICIENT, "problems": ["RUBRIC_VERSION_MISMATCH"], "detail": None}
    g1, g2 = grade_c5(ex1, docs, revenue), grade_c5(ex2, docs, revenue)
    detail = {"first": g1, "second": g2}
    if g1["value"] is None or g2["value"] is None:
        status = STATUS_INSUFFICIENT
        flags = ["REVIEW_DISAGREEMENT"] if (g1["value"] is None) != (g2["value"] is None) else []
        return {"value": None, "status": status, "problems": [], "detail": dict(detail, flags=flags, reason="ONE_OR_BOTH_NULL")}
    if g1["value"] == g2["value"]:
        return {"value": g1["value"], "status": STATUS_VERIFIED, "problems": [],
                "detail": dict(detail, flags=sorted(set(g1["flags"]) | set(g2["flags"])))}
    pick = min((g1, g2), key=lambda g: C5_SCORE[g["value"]])
    return {"value": pick["value"], "status": STATUS_DISAGREE, "problems": [],
            "detail": dict(detail, flags=sorted(set(pick["flags"]) | {"REVIEW_DISAGREEMENT"}), reason="LOWER_OF_TWO")}


def conservative(g1: dict, g2: dict) -> dict:
    """兩份判讀不一致：取較保守（分數較低；同分取證據較少者）。任一為 null → null。"""
    if g1["grade"] is None or g2["grade"] is None:
        return {"grade": None, "value": None, "flags": ["REVIEW_DISAGREEMENT"], "reason": "ONE_SIDE_NULL"}
    pick = min((g1, g2), key=lambda g: (GRADE_SCORE[g["grade"]], GRADE_RANK[g["grade"]]))
    return {"grade": pick["grade"], "value": pick["value"], "flags": sorted(set(pick["flags"]) | {"REVIEW_DISAGREEMENT"}),
            "reason": "LOWER_OF_TWO"}


def compare_dual(ex1: dict, ex2: dict, docs: list, market: str | None = None, reviews: list | None = None,
                 revenue: dict | None = None) -> dict:
    """雙抽取比對 → 欄位結果（尚未寫入）。兩份必須來自不同抽取者、同欄位同發行人同期間、第二份不得引用第一份。"""
    from t43_candidates import heterogeneity_warnings
    heterogeneity = heterogeneity_warnings(ex1, ex2)
    def finish(result):
        if heterogeneity:
            result = dict(result)
            result["warnings"] = sorted(set(result.get("warnings", [])) | set(heterogeneity))
        return result
    problems = []
    if ex1["extractor_id"] == ex2["extractor_id"]: problems.append("SAME_EXTRACTOR")
    if ex1.get("extraction_id") == ex2.get("extraction_id"): problems.append("SAME_EXTRACTION")
    for k in ("field", "issuer_id", "target_period"):
        if ex1[k] != ex2[k]: problems.append(f"MISMATCH:{k}")
    if "derived_from_extraction_id" in ex2 or "derived_from_extraction_id" in ex1: problems.append("NOT_INDEPENDENT")
    if problems:
        return finish({"value": None, "status": STATUS_INSUFFICIENT, "problems": problems, "detail": None})
    if ex1["field"] == "C5":
        return finish(_compare_dual_c5(ex1, ex2, docs, revenue))
    if ex1["field"] == "I2Q":
        return finish(_compare_dual_i2q(ex1, ex2, docs, revenue))
    if ex1["field"] in ("I2", "I4"):
        return finish(_compare_dual_guidance(ex1, ex2, docs))
    for e in (ex1, ex2):
        if e.get("rubric_version", RUBRIC_C6) not in C6_EXTRACTION_RUBRICS: problems.append("RUBRIC_VERSION_MISMATCH")
    if problems:
        return finish({"value": None, "status": STATUS_INSUFFICIENT, "problems": problems, "detail": None})
    g1, g2 = grade_c6(ex1, docs, market, reviews), grade_c6(ex2, docs, market, reviews)
    if g1["grade"] is None and g2["grade"] is None:
        return finish({"value": None, "status": STATUS_INSUFFICIENT, "problems": [], "detail": {"first": g1, "second": g2}})
    if g1["grade"] == g2["grade"] and g1["grade"] is not None:
        # 等級相同但旗標或 D1/D2 不同也算一致（分數與等級字母相同）
        return finish({"value": g1["value"], "status": STATUS_VERIFIED, "problems": [],
                       "detail": {"first": g1, "second": g2, "grade": g1["grade"], "flags": sorted(set(g1["flags"]) | set(g2["flags"])),
                                  "limitations": sorted(set(g1.get("limitations", [])) | set(g2.get("limitations", [])))}})
    pick = conservative(g1, g2)
    pick["limitations"] = sorted(set(g1.get("limitations", [])) | set(g2.get("limitations", [])))
    return finish({"value": pick["value"], "status": STATUS_DISAGREE if pick["value"] is not None else STATUS_INSUFFICIENT,
                   "problems": [], "detail": {"first": g1, "second": g2, **pick}})


def superseded_extraction_ids(extractions: list) -> set:
    """被後續抽取以 supersedes_extraction_id 取代的抽取 ID；不得再用於比對。"""
    return {e["supersedes_extraction_id"] for e in extractions if e.get("supersedes_extraction_id")}


# ----------------------------------------------------------------------------
# 欄位值作廢（T07c；仿 S3h supersede 語意：只追加、不改原列、一列最多作廢一次）
# ----------------------------------------------------------------------------
def validate_field_value_void(void: dict, values: list, voids: list = ()) -> list:
    errors = []
    by_id = {v.get("value_id"): v for v in values}
    target = by_id.get(void.get("voided_value_id"))
    if target is None: errors.append("VOIDED_VALUE_NOT_FOUND")
    if void.get("reason_code") not in VOID_REASONS: errors.append(f"VOID_REASON_UNKNOWN:{void.get('reason_code')}")
    if not void.get("voided_by") or not void.get("note"): errors.append("VOID_NEEDS_VOIDED_BY_AND_NOTE")
    if any(x.get("voided_value_id") == void.get("voided_value_id") for x in voids): errors.append("VALUE_ALREADY_VOIDED")
    # INVALID_SCOPE_ROW 只能用在 scope 確實不合格的列，不能拿來撤掉合格的欄位值
    if target is not None and void.get("reason_code") == "INVALID_SCOPE_ROW" and not scope_errors(target.get("scope")):
        errors.append("VOID_REASON_NOT_APPLICABLE:scope_is_valid")
    if void.get("reason_code") == "SUPERSEDED_BY_NEWER_VALUE" and not void.get("replacement_value_id"):
        errors.append("SUPERSEDE_NEEDS_REPLACEMENT")
    rid = void.get("replacement_value_id")
    if rid is not None:
        repl = by_id.get(rid)
        voided = {x.get("voided_value_id") for x in voids}
        if repl is None: errors.append("REPLACEMENT_NOT_FOUND")
        elif rid == void.get("voided_value_id") or rid in voided: errors.append("REPLACEMENT_INVALID")
        elif void.get("reason_code") == "SUPERSEDED_BY_NEWER_VALUE":
            # 取代不要求同一對抽取，但要同欄位、同 scope，而且替代列比被取代列新
            if target is not None and (repl.get("field"), repl.get("scope")) != (target.get("field"), target.get("scope")):
                errors.append("REPLACEMENT_MISMATCH:field_or_scope")
            elif target is not None and parse_time(repl.get("available_at")) <= parse_time(target.get("available_at")):
                errors.append("REPLACEMENT_NOT_NEWER")
        elif target is not None and (repl.get("field"), repl.get("extraction_ids")) != (target.get("field"), target.get("extraction_ids")):
            errors.append("REPLACEMENT_MISMATCH:field_or_extraction_ids")
        elif scope_errors(repl.get("scope")): errors.append("REPLACEMENT_SCOPE_INVALID")
    return errors


def load_field_value_voids(values: list | None = None, path: str | None = None) -> list:
    """讀作廢帳本並逐列檢查（雜湊、指向的欄位值存在、未重複作廢）；任何一列不合格即拒絕整份。"""
    values = read_jsonl(FIELD_VALUES) if values is None else values
    rows = read_jsonl(path or FIELD_VALUE_VOIDS)
    ids = {v.get("value_id") for v in values}
    seen = set()
    for i, r in enumerate(rows):
        if not ledger_row_ok(r): raise ValueError(f"INVALID_FIELD_VALUE_VOID:{i}:ROW_SHA256")
        if r.get("voided_value_id") not in ids: raise ValueError(f"INVALID_FIELD_VALUE_VOID:{i}:VOIDED_VALUE_NOT_FOUND")
        if r["voided_value_id"] in seen: raise ValueError(f"INVALID_FIELD_VALUE_VOID:{i}:VALUE_ALREADY_VOIDED")
        seen.add(r["voided_value_id"])
    return rows


def voided_value_ids(voids: list | None = None, values: list | None = None) -> set:
    voids = load_field_value_voids(values) if voids is None else voids
    return {v["voided_value_id"] for v in voids}


def active_field_values(values: list | None = None, voids: list | None = None) -> list:
    """讀取欄位值的唯一入口：略過被作廢的列。"""
    values = read_jsonl(FIELD_VALUES) if values is None else values
    dead = voided_value_ids(voids, values)
    return [v for v in values if v.get("value_id") not in dead]


def append_field_value_void(voided_value_id: str, reason_code: str, note: str, replacement_value_id: str | None = None,
                            created_at=None, lock_holder=None) -> dict:
    import lock as project_lock
    project_lock.require_holder(lock_holder, "ai")    # 先確認持鎖，再讀帳本驗證
    values = read_jsonl(FIELD_VALUES)
    voids = load_field_value_voids(values)
    row = {"voided_value_id": voided_value_id, "reason_code": reason_code, "replacement_value_id": replacement_value_id,
           "note": note, "voided_by": lock_holder, "created_at": created_at or now_iso()}
    errors = validate_field_value_void(row, values, voids)
    if errors: raise ValueError("INVALID_FIELD_VALUE_VOID:" + ";".join(errors))
    row["void_id"] = "AIVV_" + sha256_bytes(json.dumps([voided_value_id]).encode())[:20]   # 一列只能作廢一次
    return append_row(FIELD_VALUE_VOIDS, row, "void_id", lock_holder=lock_holder)


# ----------------------------------------------------------------------------
# 寫入（append-only）
# ----------------------------------------------------------------------------
def code_version_sha256() -> str:
    return sha256_file(os.path.abspath(__file__))


def rubric_for_field(field: str) -> str:
    return {"C6": RUBRIC_C6, "C5": RUBRIC_C5, "I2": RUBRIC_I2, "I4": RUBRIC_I4, "I2Q": RUBRIC_I2Q}[field]


def append_extraction(ex: dict, prompt_text: str, docs=None, created_at=None, lock_holder=None) -> dict:
    """驗證通過才寫入抽取帳本；補上 quote_sha256、prompt_sha256、rubric_version。"""
    docs = read_jsonl(DOCS_LEDGER) if docs is None else docs
    errors = validate_extraction(ex, docs)
    if errors: raise ValueError("EXTRACTION_INVALID:" + ";".join(errors[:6]))
    row = dict(ex)
    row["quotes"] = [dict(q, quote_sha256=sha256_bytes(normalize(q["quote"]).encode("utf-8"))) for q in ex["quotes"]]
    row["rubric_version"] = rubric_for_field(ex["field"])
    row["prompt_sha256"] = sha256_bytes(prompt_text.encode("utf-8"))
    row["created_at"] = created_at or now_iso()
    identity = json.dumps([row["field"], row["issuer_id"], row["target_period"], row["extractor_id"], row["prompt_sha256"],
                           row["doc_ids"], [q["quote_sha256"] for q in row["quotes"]]], sort_keys=True)
    row["extraction_id"] = "EXT_" + sha256_bytes(identity.encode("utf-8"))[:20]
    return append_row(EXTRACTIONS, row, "extraction_id", lock_holder=lock_holder)


def append_review(flag: str, issuer_id: str, field: str, target_period: str, quote_sha256s: list, reviewer: str, note: str,
                  created_at=None, lock_holder=None, reason_code: str | None = None) -> dict:
    """人工（或另一位審查者）複核紀錄；綁定特定引句雜湊，引句改變即失效。支援 SEGMENT_PROXY_REVIEWED 與
    SEGMENT_PROXY_REVIEW_FAILED（後者必須附 reason_code）。"""
    if flag not in (REVIEW_FLAG_SEGMENT_PROXY, REVIEW_FLAG_SEGMENT_PROXY_FAILED): raise ValueError(f"REVIEW_FLAG_UNKNOWN:{flag}")
    if not reviewer or not note: raise ValueError("REVIEW_NEEDS_REVIEWER_AND_NOTE")
    if flag == REVIEW_FLAG_SEGMENT_PROXY_FAILED and reason_code not in REVIEW_FAIL_REASONS: raise ValueError(f"REVIEW_FAIL_REASON_UNKNOWN:{reason_code}")
    if flag == REVIEW_FLAG_SEGMENT_PROXY and reason_code is not None: raise ValueError("REVIEW_REASON_ONLY_FOR_FAILED")
    row = {"flag": flag, "issuer_id": issuer_id, "field": field, "target_period": target_period,
           "quote_sha256s": sorted(quote_sha256s), "reviewer": reviewer, "note": note, "created_at": created_at or now_iso()}
    if reason_code: row["reason_code"] = reason_code
    row["review_id"] = "REV_" + sha256_bytes(json.dumps([flag, issuer_id, field, target_period, row["quote_sha256s"]]).encode())[:20]
    return append_row(REVIEWS, row, "review_id", lock_holder=lock_holder)


def append_value_annotation(*, field: str, scope: str, extraction_ids: list, basis: list, reviewer: str, note: str,
                            value_id: str | None = None, independence: str | None = None, verification: str | None = None,
                            coverage: str | None = None, created_at=None, lock_holder=None) -> dict:
    """T20：欄位值標註（獨立性、複查者原文重算、無法產生欄位值的原因）。只追加；報告與快照讀欄位值時必須一併帶出。"""
    import lock as project_lock
    project_lock.require_holder(lock_holder, "ai")
    errors = []
    if independence is None and verification is None and coverage is None: errors.append("ANNOTATION_EMPTY")
    if independence is not None and independence not in INDEPENDENCE_LEVELS: errors.append(f"INDEPENDENCE_UNKNOWN:{independence}")
    if verification is not None and verification not in VERIFICATION_CODES: errors.append(f"VERIFICATION_UNKNOWN:{verification}")
    if coverage is not None and coverage not in COVERAGE_CODES: errors.append(f"COVERAGE_UNKNOWN:{coverage}")
    if independence == "INDEPENDENCE_PARTIAL" and verification is None: errors.append("PARTIAL_NEEDS_VERIFICATION")
    if not basis or not all(isinstance(b, str) and b for b in basis): errors.append("ANNOTATION_NEEDS_BASIS")
    if not reviewer or not note: errors.append("ANNOTATION_NEEDS_REVIEWER_AND_NOTE")
    active = {v["value_id"]: v for v in active_field_values()}
    if value_id is not None:
        v = active.get(value_id)
        if v is None: errors.append("ANNOTATED_VALUE_NOT_ACTIVE")
        elif (v["field"], v["scope"], v["extraction_ids"]) != (field, scope, list(extraction_ids)):
            errors.append("ANNOTATION_VALUE_MISMATCH")
        if coverage is not None: errors.append("COVERAGE_ONLY_WITHOUT_VALUE")
    else:
        if coverage is None: errors.append("ANNOTATION_NEEDS_VALUE_ID")
        if any(v["field"] == field and v["scope"] == scope for v in active.values()):
            errors.append("ACTIVE_VALUE_EXISTS_ANNOTATE_IT")
        exs = {e["extraction_id"]: e for e in read_jsonl(EXTRACTIONS)}
        for eid in extraction_ids:
            e = exs.get(eid)
            if e is None or e["field"] != field or e["issuer_id"] != scope: errors.append(f"ANNOTATION_EXTRACTION_MISMATCH:{eid}")
    errors += scope_errors(scope)
    if errors: raise ValueError("INVALID_ANNOTATION:" + ";".join(errors))
    row = {"flag": REVIEW_FLAG_ANNOTATION, "field": field, "scope": scope, "value_id": value_id,
           "extraction_ids": list(extraction_ids), "independence": independence, "verification": verification,
           "coverage": coverage, "basis": list(basis), "reviewer": reviewer, "note": note, "created_at": created_at or now_iso()}
    row["review_id"] = "REVA_" + sha256_bytes(json.dumps([REVIEW_FLAG_ANNOTATION, field, scope, value_id, row["extraction_ids"],
                                                          independence, verification, coverage]).encode())[:20]
    return append_row(REVIEWS, row, "review_id", lock_holder=lock_holder)


def _checked_reviews(reviews: list | None = None) -> list:
    reviews = read_jsonl(REVIEWS) if reviews is None else reviews
    for i, r in enumerate(reviews):
        if not ledger_row_ok(r): raise ValueError(f"INVALID_REVIEW_ROW:{i}:ROW_SHA256")
    return reviews


def value_annotations(reviews: list | None = None) -> list:
    return [r for r in _checked_reviews(reviews) if r.get("flag") == REVIEW_FLAG_ANNOTATION]


def extraction_annotations(reviews: list | None = None) -> list:
    return [r for r in _checked_reviews(reviews) if r.get("flag") == REVIEW_FLAG_EXTRACTION_ANNOTATION]


def append_extraction_annotation(*, extraction_id: str, annotation_type: str, effective_as_of: str, direction: str,
                                 basis: list, reviewer: str, note: str, created_at=None, lock_holder=None) -> dict:
    """T20b：抽取層標註。目前只有 AS_OF_METADATA_ERROR：as_of 晚於 created_at，有效 as_of 以 created_at 為準。"""
    import lock as project_lock
    project_lock.require_holder(lock_holder, "ai")
    errors = []
    extractions = read_jsonl(EXTRACTIONS)
    ex = next((e for e in extractions if e["extraction_id"] == extraction_id), None)
    if annotation_type not in EXTRACTION_ANNOTATION_TYPES: errors.append(f"ANNOTATION_TYPE_UNKNOWN:{annotation_type}")
    if direction not in AS_OF_DIRECTIONS: errors.append(f"DIRECTION_UNKNOWN:{direction}")
    if not basis or not all(isinstance(b, str) and b for b in basis): errors.append("ANNOTATION_NEEDS_BASIS")
    if not reviewer or not note: errors.append("ANNOTATION_NEEDS_REVIEWER_AND_NOTE")
    if ex is None:
        errors.append("ANNOTATED_EXTRACTION_NOT_FOUND")
    else:
        if extraction_id in superseded_extraction_ids(extractions): errors.append("EXTRACTION_SUPERSEDED")
        if annotation_type == "AS_OF_METADATA_ERROR":
            if not ex.get("as_of") or not ex.get("created_at") or parse_time(ex["as_of"]) <= parse_time(ex["created_at"]):
                errors.append("AS_OF_NOT_LATER_THAN_CREATED_AT")
            if effective_as_of != ex.get("created_at"): errors.append("EFFECTIVE_AS_OF_MUST_EQUAL_CREATED_AT")
    if errors: raise ValueError("INVALID_ANNOTATION:" + ";".join(errors))
    row = {"flag": REVIEW_FLAG_EXTRACTION_ANNOTATION, "type": annotation_type, "extraction_id": extraction_id,
           "field": ex["field"], "scope": ex["issuer_id"], "recorded_as_of": ex["as_of"], "effective_as_of": effective_as_of,
           "direction": direction, "basis": list(basis), "reviewer": reviewer, "note": note, "created_at": created_at or now_iso()}
    row["review_id"] = "REVX_" + sha256_bytes(json.dumps([REVIEW_FLAG_EXTRACTION_ANNOTATION, annotation_type, extraction_id]).encode())[:20]
    return append_row(REVIEWS, row, "review_id", lock_holder=lock_holder)


def report_field_values(values: list | None = None, voids: list | None = None, reviews: list | None = None) -> dict:
    """報告或快照讀欄位值的入口：略過作廢列，並把標註一起帶出（引用時必須附上）。
    annotations＝欄位值層級標註；extraction_annotations＝這列所用抽取的標註（例：as_of 中繼資料錯誤）；
    unattached_annotations＝沒有欄位值列可掛的標註（例：第二次抽取寫不出、兩邊都是 null）。"""
    rows = active_field_values(values, voids)
    reviews = _checked_reviews(reviews)
    notes, ex_notes = value_annotations(reviews), extraction_annotations(reviews)
    by_value, by_ex = {}, {}
    for n in notes:
        if n.get("value_id"): by_value.setdefault(n["value_id"], []).append(n)
    for n in ex_notes: by_ex.setdefault(n["extraction_id"], []).append(n)
    return {"values": [dict(v, annotations=by_value.get(v["value_id"], []),
                            extraction_annotations=[n for i in v["extraction_ids"] for n in by_ex.get(i, [])]) for v in rows],
            "unattached_annotations": [n for n in notes if not n.get("value_id")],
            "extraction_annotations": ex_notes}


def append_field_value(ex1_row: dict, ex2_row: dict, docs=None, scope=None, created_at=None, reviews=None,
                       lock_holder=None, revenue=None) -> dict:
    docs = read_jsonl(DOCS_LEDGER) if docs is None else docs
    reviews = read_jsonl(REVIEWS) if reviews is None else reviews
    errors = scope_errors(scope or ex1_row["issuer_id"])
    if not errors and scope and scope in known_scopes()[0] and scope != ex1_row["issuer_id"]:
        errors = [f"SCOPE_ISSUER_MISMATCH:{scope}!={ex1_row['issuer_id']}"]
    if errors: raise ValueError("INVALID_SCOPE:" + ";".join(errors))
    stale = superseded_extraction_ids(read_jsonl(EXTRACTIONS)) & {ex1_row["extraction_id"], ex2_row["extraction_id"]}
    if stale: raise ValueError("EXTRACTION_SUPERSEDED:" + ",".join(sorted(stale)))
    result = compare_dual(ex1_row, ex2_row, docs, None, reviews, revenue)
    now = created_at or now_iso()
    row = {"field": ex1_row["field"], "scope": scope or ex1_row["issuer_id"], "value": result["value"],
           "status": result["status"], "grade_detail": result.get("detail"), "problems": result.get("problems", []),
           "extraction_ids": [ex1_row["extraction_id"], ex2_row["extraction_id"]],
           "doc_ids": sorted(set(ex1_row["doc_ids"]) | set(ex2_row["doc_ids"])),
           "review_ids": sorted(r["review_id"] for r in reviews if r.get("issuer_id") == ex1_row["issuer_id"]
                                and r.get("field") == ex1_row["field"]),
           "limitations": (result.get("detail") or {}).get("limitations", []),
           "computed_by": code_version_sha256(),
           "rubric_version": rubric_for_field(ex1_row["field"]),      # 判級所用的判定表版本
           "extraction_rubric_versions": [ex1_row["rubric_version"], ex2_row["rubric_version"]],
           "valid_from": now, "valid_until": None,                  # 有效期間隨「最近 2 份財報」移動，讀取時用 is_current 判斷
           "available_at": now, "evidence_kind": "RETROSPECTIVE_EXTRACTION"}
    row["value_id"] = "AIV_" + sha256_bytes(json.dumps([row["field"], row["scope"], row["extraction_ids"],
                                                      row["review_ids"]]).encode())[:20]
    return append_row(FIELD_VALUES, row, "value_id", lock_holder=lock_holder)


def is_current(value_row: dict, docs: list, voids: list | None = None) -> bool:
    """文件集合沒變（沒有新財報讓有效期間移動）才算現行；否則視為 null，須重新抽取。被作廢的列永遠不是現行。"""
    if value_row.get("value_id") in voided_value_ids(voids): return False
    return set(effective_docs(value_row["scope"], docs)["doc_ids"]) == set(value_row["doc_ids"])


def is_current_for_field(value_row: dict, docs: list, voids: list | None = None) -> bool:
    """Field-specific currentness used by the 1.13.1 candidate.

    C6 remains bound to the complete two-report evidence window.  C5, I2 and
    I2Q instead describe one reporting release: every financial-report document
    used by the value must belong to the issuer's latest registered report
    period.  A stale latest value is null; callers must never fall back.
    """
    if value_row.get("value_id") in voided_value_ids(voids):
        return False
    field = value_row.get("field")
    if field == "C6":
        return is_current(value_row, docs, voids)
    if field not in {"C5", "I2", "I2Q"}:
        return is_current(value_row, docs, voids)
    issuer = value_row.get("scope")
    issuer_docs = [d for d in docs if d.get("issuer_id") == issuer and d.get("report_period_end")]
    # C5 is defined against the latest filed financial report.  I2/I2Q are
    # defined against the latest earnings release and therefore also accept an
    # official earnings release/transcript carrying report_period_end.
    reports = ([d for d in issuer_docs if d.get("doc_type") in REPORT_DOC_TYPES]
               if field == "C5" else issuer_docs)
    if not reports:
        return False
    latest_period = max(d["report_period_end"] for d in reports)
    by_id = {d.get("doc_id"): d for d in reports}
    used = [by_id[doc_id] for doc_id in value_row.get("doc_ids", []) if doc_id in by_id]
    if not used:
        return False
    if field == "C5":
        return all(d.get("report_period_end") == latest_period for d in used)
    # I2/I2Q compare the latest release with an earlier guidance document.
    # The earlier comparison document is expected; the value is current when
    # the newest report period used by the pair is the issuer's newest period.
    return max(d.get("report_period_end") for d in used) == latest_period


def recompute_value(value_row: dict, extractions: list, docs: list, reviews: list | None = None,
                    revenue: dict | None = None) -> dict:
    """重播：只用抽取帳本與文件帳本重新算出欄位值，必須和已存值相同。被作廢的列不重播。"""
    if value_row.get("value_id") in voided_value_ids(): raise ValueError(f"FIELD_VALUE_VOIDED:{value_row.get('value_id')}")
    by_id = {e["extraction_id"]: e for e in extractions}
    ex1, ex2 = (by_id[i] for i in value_row["extraction_ids"])
    reviews = [r for r in (reviews if reviews is not None else read_jsonl(REVIEWS)) if r["review_id"] in value_row.get("review_ids", [])]
    return compare_dual(ex1, ex2, docs, None, reviews, revenue)
