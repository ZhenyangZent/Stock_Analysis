# 研究收件匣

這裡收集影片、Podcast、RSS 與公開文章，供後續查證。收件匣是非計分區：內容不會寫入 `C5`、`C6`、`I2`、`I2Q`、`I3` 或 `I4`，也不會改變正式分數。

## 加入來源

編輯 `sources.json` 的 `sources` 陣列。每個來源需要：

- `source_id`：穩定、不重複的 ID。
- `name`：顯示名稱。
- `type`：`youtube`、`podcast`、`rss` 或 `article`。
- `url`：原始網址。
- `language`：例如 `zh-Hant` 或 `en`。
- `member_only`：是否為會員內容。
- `enabled`：只有 `true` 會被 `fetch` 處理。

內建範例全部為 `enabled: false`；請改成真實來源後再啟用。

## 執行

需要 Python 3.13，不需要額外套件。指令的 stdout 永遠是單一 JSON 摘要，含新增、失敗與人工處理數量。

```powershell
python 03_工具/research_inbox.py validate-sources
python 03_工具/research_inbox.py fetch
python 03_工具/research_inbox.py ingest-yt --source-id my-channel --input-dir "D:/yt-downloader/output"
```

`fetch` 只抓公開、已啟用的 RSS／Podcast／文章，先檢查 `robots.txt`，並使用誠實識別的 User-Agent。YouTube 不重做下載；請將現有 `yt-downloader` 的輸出目錄交給 `ingest-yt`。工具讀取 `*.info.json` 及同名字幕，並以內容 SHA-256 去重。

## 會員內容

會員內容必須在 `sources.json` 標示 `member_only: true`。工具不會複製影片、字幕、逐字稿或 Cookie；`inbox.jsonl` 只保存原始外部檔案的絕對路徑、SHA-256 與 `MEMBER_ONLY_PERSONAL_USE` 標記。不要將 `cookies.txt` 放進本專案。

## 待查證問題

先從 `inbox.jsonl` 找到 `record_id`，再追加問題：

```powershell
python 03_工具/research_inbox.py add-question --record-id RINBOX_... --question "X 公司 AI 營收佔 30% 嗎？"
python 03_工具/research_inbox.py update-question --question-id RQ_... --status VERIFIED_OFFICIAL --official-doc-id DOC_...
```

問題與狀態事件寫入只追加的 `verification_questions.jsonl`。狀態只能是 `OPEN`、`VERIFIED_OFFICIAL`、`CONTRADICTED` 或 `UNVERIFIABLE`；官方查證只連結 `official_doc_ids`，不寫正式欄位。

## 檔案契約

- `inbox.jsonl`：只追加的內容紀錄。
- `verification_questions.jsonl`：只追加的問題／狀態事件，首次需要時才建立。
- `transcripts/`：只保存公開內容的標準化 UTF-8 純文字。

同一 `source_id + original_url` 或同一 `content_sha256` 不會再登錄。工具本身不寫 `01_共用資料/`、`02_執行紀錄/`、`04_回測/` 或任何 AI 欄位帳本。
