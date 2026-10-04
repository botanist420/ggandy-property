# GGAndy 代辦清單

> 2026-10-04 依程式碼與本機 `ggandy_dev` 盤點建立。完成後打勾並寫上 commit / 日期；新的想法直接往下加。
> 優先度：🔴 會算錯或用不了 / 🟠 流程缺口 / 🟡 改善 / ⚪ 環境與文件

## 🔴 Bug：資料會算錯或功能用不了

- [ ] **Vendor Bill 上看不到 GGAndy 欄位**
  `views/account_move_views.xml` 把所有 GGAndy 欄位塞進 `sale_info_group`，但 Odoo 19 這個 group 設了 `invisible="move_type not in ('out_invoice', 'out_refund')"`。結果房東合約、**費用歸屬物件／單位／費用類型**在 Vendor Bill 上都看不到，使用者沒辦法手動標記採購費用與員工代墊，帳務總表的「採購費用」「員工代墊」永遠是 0。
  → 另開一個 vendor bill 專用 group（例如 xpath 到 `//group[@name='accounting_info_group']` 前面，`invisible="move_type not in ('in_invoice','in_refund')"`）。
- [ ] **終止／取消的租約，未來期次還被算進帳務總表的「租金應收」**
  `accounting_overview._compute_amounts` 用單位 + 月份抓期次，沒過濾 `lease_id.state`。租約提前終止後，剩下的期次還在，會讓應收、未收、現金流判讀失真。
  → 終止時刪除（或標記）`period_start > 終止日` 且未開單的期次，並在總表排除 `cancelled` 租約的期次。
- [ ] **租約沒有辦法變成「已到期」**
  `ggandy.lease` 有 `expired` 狀態，但沒有按鈕也沒有排程會設定它。租期結束後租約永遠是 `active`，單位也一直是「已出租」，Telegram 的到期提醒只看得到 30 天內的。
  → 新增 `action_mark_expired` + 每日 cron：`end_date < today` 的 active 租約 → expired，並 `_refresh_from_active_leases()`。房東合約也一樣只有手動「標記到期」，可以一起處理。
- [ ] **Telegram `/status` 的逾期戶數和 `/overdue` 對不起來**
  `_build_status_message` 自己寫篩選，沒排除非 active 租約、也沒排除已取消發票；`/overdue` 用的是 `_get_overdue_domain()`。
  → 改成 `Schedule.search_count(company_domain + Schedule._get_overdue_domain())`。

## 🟠 流程缺口

- [ ] **55 / 59 個物件的房東是預設的系統 Bot（`base.partner_root`）**
  Google Sheet 匯入精靈的預設房東是 `base.partner_root`，而且每次重新匯入都會**覆寫** `owner_id / manager_id / management_mode`，手動修正過的房東會被蓋回去。
  → (1) 匯入時只在新建時寫入預設房東／管理人／經營模式；(2) 預設房東改成必填、不給預設值，或在 Sheet 加「房東」欄位；(3) 清理現有資料。
- [ ] **從 Google Sheet 匯入聯絡人**：選單已存在，`action_import_contacts` 只會丟 `UserError`。可把 `scripts/google_res_partner.py` 的邏輯（欄位對應、依 ref/email/mobile/phone/name 找既有聯絡人）搬進 wizard。
- [ ] **報修費用沒有進帳務**：`charged_to`、`actual_cost` 只是記錄。可在完成報修時依費用歸屬：公司／房東負擔 → 建 vendor bill（帶 `ggandy_expense_*`），房客負擔 → 加到下一期租金或另開 invoice；房東負擔的部分從代管結算扣除。
- [ ] **逾期活動不會自動結案**：收款後「逾期租金待處理」活動還在，需在付款後（或 cron 中）`action_feedback` 關掉。
- [ ] **月中入住／退租的期次不按天數計算**：目前第一期、最後一期都是完整月租。確認公司實際收法後決定要不要按日比例。
- [ ] **租約生效會觸發全域開帳單**：`lease.action_activate` 最後呼叫 `_cron_create_due_invoices()`，會順便替**所有**租約開帳單。改成只處理 `self.schedule_ids`。
- [ ] **`action_terminate` 沒檢查狀態**：可以對 draft / cancelled 租約呼叫（UI 有擋，RPC 沒擋），也沒記錄實際終止日。考慮加 `termination_date` 欄位。
- [ ] **押金退還流程**：目前押金只在帳務總表算入現金流，沒有收押金 / 退押金的帳單或負債科目。
- [ ] **房東押金 `owner_deposit`** 只是記錄欄位，沒有付款流程。

## 🟡 改善

- [ ] **自動化測試**：兩個模組都沒有 `tests/`。最值得先寫：`_generate_rent_schedule`（短月、月中起訖）、`_get_owner_settlement_amount`（包租／代管百分比／固定費／部分收款）、`_compute_amounts`（分攤比例）、重疊檢查 constraint。
- [ ] **帳務總表效能**：`_search_computed_flag` 是 `self.search([]).filtered(...)`，每次篩選都會算整張表所有列。資料量大後考慮改成 stored + cron 重算，或用 SQL view（`_auto = False`）。
- [ ] **Google Sheet URL 寫死在程式碼**（`DEFAULT_PROPERTY_SHEET_URL`）：改成 `ir.config_parameter` 或設定頁欄位。
- [ ] **Telegram 正式環境**：目前只有 getUpdates 輪詢（每分鐘，和其他 cron 共用 `max_cron_threads = 1`）。正式站可加 webhook controller；`TelegramAPIClient.get_updates` 可用 long polling `timeout`。
- [ ] Telegram 訊息改用 `parse_mode=HTML` 做粗體／連結，並加上 Odoo 記錄的深層連結。
- [ ] 物件的 `owner_payment_day` 改了之後，是否要同步到生效中的房東合約？（目前只在 create / onchange 帶入）
- [ ] `scripts/ggandy_common.sql` 補上 `owner_payment_day`、照片附件相關查詢。

## ⚪ 環境與文件

- [ ] **`odoo.conf` 的 `admin_passwd` 被 git 追蹤**：本機已改成真實密碼（未 commit）。建議改成 `odoo.conf.example` 進 git、`odoo.conf` 加進 `.gitignore`，或改用環境變數。
- [x] ~~Notebook 無法在容器內執行~~ → 2026-10-04 決定不再使用 Jupyter：playground 改寫成 `notebook/scripts/orm_playground.py`，刪除 ipynb 與 `notebook/.vscode`，腳本用 stdin 餵 `odoo shell`，不需要 notebook mount。
- [ ] `docker-compose.yml` 新增的 PostgreSQL `5555` port 尚未 commit；確認要保留後再 commit（文件已按 5555 撰寫）。
- [ ] `scripts/__pycache__/` 出現在工作目錄（已被 gitignore 的 `__pycache__/` 涵蓋，不影響 git，可刪）。
