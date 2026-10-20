# 最小資料脊椎資料區

版本 1.2.1。這裡的檔案是 canonical 結構，不是 Excel 的替代副本。

- `entity_registry.json`：發行人與證券主檔。TSM 與 2330 共用發行人，但證券、幣別與價格序列不同。
- `events.jsonl`：事件原始日期、時間、時區與三個資料時間。只新增；更正用新 ID 指向舊 ID。
- `observations.jsonl`：point-in-time 指標資料。只新增；不得替舊資料補造 `available_at`。
- `market_sessions.csv`：交易 session 與 append-only 修訂。
- `calendar_versions.jsonl`：每版日曆的 SHA-256 與列數，供舊 run 重驗。
- `fx_rates.csv`：帶 fixing 與 available 時間的匯率。

目前事件、observation、session 與匯率表只建立結構，尚未匯入正式資料。缺資料時工具必須回傳資料不足，不得以日曆日、今天的資料或人工估計補值。

`01_共用資料/portfolio.json` 不在此資料夾內，且只有使用者能提供或修改。檔案不存在代表 `NOT_PROVIDED`；AI 不得代建空檔。

新聞與情緒模組維持停用；Excel 維持 legacy 研究檢視，1.2.1 未重構活頁簿。Claude 複查 1.2.1 通過前不得匯入正式資料或建立正式 schema 2 紀錄。
