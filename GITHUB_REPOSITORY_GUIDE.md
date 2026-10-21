# GitHub repository 內容說明

這個 repository 保存「可審查、可執行、可重建」的研究系統核心，不是本機資料夾的逐位元備份。

## 已納入

- 00_規範：現行規則、歷史規則、使用者決定與版本紀錄。
- 01_共用資料：結構化 canonical observations、價格、總經、匯率、事件、AI 欄位與寫入帳本；T46 污染檔除外。
- 02_執行紀錄：正式研究快照與必要執行紀錄；CAS cache、暫存與自動迴圈輸出除外。
- 03_工具：抓取、驗證、研究、估值、風險與回歸測試程式。
- 04_回測、05_研究收件匣：可分享的研究輸出與收件匣結構。
- 根目錄操作規範與完整 GPT 交接文件。

## 未納入

### 私密資料

- 所有 .env、API key、私鑰。
- portfolio.json；持股資料只屬於使用者，不進版本庫。
- 執行中的協作鎖。

### 可重新取得或不適合散布的大型材料

- 01_共用資料/data_spine/raw：SEC、MOPS、IR 等原始文件。
- 99_原始材料、05_意見來源。
- PDF、HTML／XHTML、影片、字幕與壓縮檔。
- 02_執行紀錄/影子快照/_cas：content-addressed 快照 cache。
- 90_各AI工作區的大量測試暫存、重複下載與一次性交接草稿。

這些材料仍保留在使用者本機。canonical ledger 保留來源 URL、文件 ID、雜湊與抓取時間；需要時應由合規 fetcher 重建，不可繞過 robots.txt、登入、驗證碼或付費牆。

## T46 已知阻擋

2026-10-20 的 T46 首次掃描誤把 599 份歷史文件當成新文件。以下目前已知受污染，因此刻意不提交：

- 01_共用資料/data_spine/docs_ledger.jsonl
- 01_共用資料/data_spine/staging_write_audit_docs.jsonl
- 02_執行紀錄/待抽取清單.json

污染前 docs ledger 的已驗證狀態為 107 列、108,430 bytes、SHA-256：

e5d5e81a1859a0b4a708c9880428c1353be51b74820451ac13d127219acd87b9

最新正式快照建立於污染以前，仍可作為當時狀態的證據。復原需要使用者另行授權；不得從 GitHub 缺少這三個檔案推論它們為空，也不得自行建立替代版本。

## Clone 後需要自行提供

1. 在專案外建立 `%USERPROFILE%/.ai_stock/.env`。
2. 視需要設定 TIINGO_API_KEY、FRED_API_KEY；不要把值提交。
3. 透過 fetcher 重新取得允許下載的原始文件。
4. portfolio.json 只能由使用者本人建立。
5. 執行任何共用寫入前，先依協作鎖定.md 取得鎖。

## 研究定位

本專案輸出的是「可列入研究／等待／資料不足」等研究分類，不是買賣指令、目標價或報酬保證。回歸測試全綠只表示程式符合規格，不表示投資方法已被驗證。

## Durable handoff evidence

- [START_HERE](90_各AI工作區/codex/START_HERE.md) is the permanent navigation entry; it is not a replacement for formal decisions or rules.
- [HANDOFF_POLICY](90_各AI工作區/codex/HANDOFF_POLICY.md) defines evidence states, live-state refresh, conflict-stop and supersede requirements.
- Small, legal, non-sensitive templates and T46 derivative corrections may be tracked after review.
- Historical `PASS` applies only to its bound code/input/rule/environment hashes; takeover requires fresh live-state readback.
- Large/restricted source files remain outside public Git. Future external evidence indexing, historical ADR backfill, CI and automated restore drills are separate phases requiring separate authority.
- T46 incident evidence remains immutable and blocked; a correction report does not constitute recovery.
