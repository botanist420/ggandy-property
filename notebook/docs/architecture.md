# GGAndy 系統架構與資料模型

> 對應程式碼版本：2026-10（`ggandy_property_management` 19.0.1.3.0、`ggandy_property_telegram` 19.0.1.0.2、`ggandy_property_project` 19.0.1.1.0）。
> 程式有改動時請同步更新本文件。

## 1. 模型關係圖

```text
                         res.partner
          (is_ggandy_owner / is_ggandy_tenant / is_ggandy_vendor)
             ▲ owner_id          ▲ tenant_id            ▲ vendor_id
             │ co_owner_ids(M2M) │ co_tenant_ids(M2M)   │
             │                   │                      │
ggandy.owner.contract ──property_id──► ggandy.property ◄──property_id── ggandy.maintenance.request
   │ vendor_bill_ids                    │ unit_ids                          │ unit_id / lease_id
   ▼                                    ▼                                   │
account.move (in_invoice)        ggandy.property.unit ◄──────────────────────┘
 ggandy_owner_contract_id               │ lease_ids
 ggandy_settlement_period_start/end     ▼
                                  ggandy.lease
                                        │ schedule_ids
                                        ▼
                                  ggandy.rent.schedule ──invoice_id──► account.move (out_invoice)
                                                                         ggandy_lease_id

account.move (in_invoice，採購 / 員工代墊)
 ggandy_expense_property_id / ggandy_expense_unit_id / ggandy_expense_kind

ggandy.accounting.overview（unit_id × period_start）── 讀取以上所有資料即時計算
```

## 2. 各模型重點

### ggandy.property（物件）

| 欄位 | 說明 |
| --- | --- |
| `code` | `PROP/%(year)s/0001`，create 時取號 |
| `property_type` | building / apartment / house / suite / commercial / other |
| `management_mode` | master_lease（包租）/ agency（代管）/ mixed（混合） |
| `owner_id` | 主要房東，domain `is_ggandy_owner = True`，`ondelete=restrict` |
| `co_owner_ids` | 共同屋主（`ggandy_property_co_owner_rel`） |
| `manager_id` | 內部管理人員，逾期活動優先指派給他 |
| `owner_payment_day` | 匯款給房東的日期（1–31，0 = 未設定），房東合約 create / onchange 時預設帶入 |
| `acquisition_date` / `management_end_date` | 房東合約生效時回寫 |

### ggandy.property.unit（出租單位）

| 欄位 | 說明 |
| --- | --- |
| `unit_type` | suite / room / whole / shop / office / parking / other |
| `monthly_rent` | 參考月租：租約預設值，也是帳務總表房東應付的**分攤權重** |
| `deposit_months` | 押金月數（預設 2） |
| `electricity_billing_type` / `water_billing_type` | metered（依度數）/ fixed（每月固定）/ included（含租金）/ tenant_paid（房客自繳）；空白 = 未設定，無預設值 |
| `electricity_rate` / `water_rate` | 每度單價（Float, digits (10,2)，不用 Monetary 以免被幣別小數位四捨五入）；費用 = (本月度數 − 上月度數) × 單價 |
| `electricity_fixed_fee` / `water_fixed_fee` | 每月固定水電費（Monetary） |
| `electricity_meter_ref` / `water_meter_ref` | 電錶／水錶編號或位置 |
| `state` | vacant / reserved / occupied / maintenance / inactive |
| `layout_photo_ids` | compute：本單位 chatter 上的 `image/*` 附件 |
| `maintenance_photo_ids` | compute：本單位所有報修單（含封存）的 `image/*` 附件，依 `ggandy_request_date` desc |

`_refresh_from_active_leases()`：只在 `vacant` / `occupied` 狀態下，依是否有 active 租約自動切換。

### ggandy.owner.contract（房東合約）

狀態：`draft → active → expired / terminated`，`draft → cancelled`，`terminated / cancelled → draft`。

| 欄位 | 說明 |
| --- | --- |
| `contract_type` | master_lease（包租）/ agency（代管） |
| `owner_payment_day` | 每月結算日（1–31，預設 10，或從物件帶入） |
| `guaranteed_rent` | 包租保底租金（生效前必填 > 0） |
| `fee_type` / `fee_rate` / `fixed_fee` | 代管費：百分比（填 10 = 10%）或每月固定金額 |
| `owner_deposit` | 公司付給房東的押金（僅記錄，不進結算） |

**每月房東應付計算** `_get_owner_settlement_amount`：

```text
包租：guaranteed_rent
代管：collected = Σ(期次 rent_amount × 該期 invoice 已付比例)
        （只算 period_start 落在當月、invoice 已過帳的期次；不含管理費）
      fee = collected × fee_rate / 100        （percentage）
          = fixed_fee if collected else 0     （fixed）
      amount = max(collected − fee, 0)
```

`_cron_create_owner_vendor_bills`：今天 ≥ 當月結算日（短月取月底）且合約期間涵蓋當月 → 建 `in_invoice`；同合約同 `ggandy_settlement_period_start` 已有非取消帳單則跳過；金額 ≤ 0 不建立。

### ggandy.lease（房客租約）

狀態：`draft → active → expired / terminated`，`draft → cancelled`，`→ draft`（無已過帳帳單才可）。

水電：`electricity_billing_type` / `water_billing_type` related 出租單位（給表單與期次列表 `column_invisible` 用）；`electricity_start_reading` / `water_start_reading` 為入住度數，onchange 出租單位時預設帶 `unit._get_last_meter_reading()`（該單位跨租約最近一筆 curr ≠ 0 的期次）。

`_generate_rent_schedule()` 拆月規則：

```text
for 每個月 month_cursor（start_date 月 ~ end_date 月）:
    period_start = max(start_date, 月初)
    period_end   = min(end_date, 月底)
    due_date     = 該月 rent_due_day（短月取月底），若早於 start_date 則用 start_date
    ratio        = 期次天數 ÷ 當月實際天數（整月 = 1）
    rent_amount / management_fee = 月額 × ratio，四捨五入到元（整月直接用月額）
    水電設定 = 出租單位快照（固定水電費同樣 × ratio）；prev_reading = _get_meter_reading_before()
    同 lease + period_start 已存在 → 跳過（可重複執行）
```

> 按日比例用**當月實際天數**（不是固定 30 天）：一年期租約起訖在同一個月份，第一期 + 最後一期剛好等於一個月，總額 = 12 個月租金。
> 例：9/25 起租、月租 31,000 → 第一期 9/25–9/30 = 31,000 × 6/30 = 6,200。
> 規則只影響新產生的期次；改版前已建立的期次維持原金額。

`contract_document_ids`：非 stored compute，抓 `res_model = ggandy.lease`、`res_id = 本租約` 的所有 `ir.attachment`（不限圖片），一次 search 後依 `res_id` 分配；未儲存的新租約為空。

#### 租約 PDF（`report/lease_contract_report.*`）

- `ir.actions.report` `action_report_ggandy_lease_contract`（qweb-pdf，綁在租約的「列印」選單，表單 header 也有按鈕），紙張用自訂的 `paperformat_ggandy_lease_contract`（A4、四邊 18mm，因為 `web.basic_layout` 沒有頁首）。
- 資料由 AbstractModel `report.ggandy_property_management.report_lease_contract` 的 `_get_report_values` 準備：
  - 出租人判斷：找該物件在租約開始日有效的 active 房東合約 → 用 `contract_type`；找不到就用物件 `management_mode`。`master_lease` → 出租人 = 公司 partner、沒有丙方；其他（代管 / 混合）→ 出租人 = 物件 `owner_id`、丙方 = 公司 partner。
  - 民國日期、`新臺幣 x,xxx 元` 金額、出租單位描述、通訊地址都在 Python 組好字串，避免 XML 換行在中文裡變成空格。
- 字型用容器內的 `Noto Serif CJK TC`；每一條用 `page-break-inside: avoid` 避免標題和內容被拆到兩頁。
- 只下載、不自動存附件（範本沒設 `attachment`），避免每按一次就在 chatter 多一份。

### ggandy.rent.schedule（租金期次）

| 欄位 | 說明 |
| --- | --- |
| `{electricity,water}_billing_type` / `_rate` / `_fixed_fee` | 建期次時從出租單位**快照**（固定費按日比例）；單位 `write()` 改這些設定時，`_sync_utility_settings_to_schedules()` 會更新租約 draft/active、帳單未過帳的期次 |
| `{electricity,water}_prev_reading` / `_curr_reading` | 上期／本期度數（Float digits (12,1)）。第一期 prev = 租約 `*_start_reading`，之後 = 前一期的 curr（沒抄就沿用前一期的 prev） |
| `{electricity,water}_usage` / `_amount` | stored compute：metered = (curr − prev) × rate 四捨五入到元（curr 為 0 視為未抄、金額 0）；fixed = fixed_fee；其他 = 0 |
| `meter_reading_pending` | stored compute：任一項 metered 且 curr 為 0（「待抄表」篩選用） |
| `total_amount` | stored compute = `rent_amount + management_fee + electricity_amount + water_amount` |
| `invoice_id` | 建立的客戶發票（租金、管理費、電費、水費各一行，0 元不列） |
| `collection_state` | 非 stored compute：uninvoiced / draft / unpaid / partial / paid / overdue / cancelled |

- **度數連動**：`write()` 改 prev/curr 後呼叫 `_propagate_meter_reading()`，把讀數往後帶到同租約之後的期次，直到遇到下一筆已抄表（curr ≠ 0）的期次為止；已過帳的期次不動。租約改 `*_start_reading` 也用同一套（`_apply_previous_reading`）。
- **鎖定與同步**：帳單已過帳時改水電欄位 → `UserError`；帳單是草稿時 `_sync_draft_invoice_utility_lines()` 以產品比對更新／新增／刪除水電行。
- `@api.constrains`：curr 不可小於 prev（換錶要手動改 prev）。
- `_get_unpaid_domain()`、`_get_overdue_domain()`：共用的 domain，Telegram 也用。
- `_cron_create_due_invoices`：`invoice_id = False`、`period_start <= today`、租約 active → `action_create_invoice()`。
- `_cron_create_overdue_rent_activities`：符合 overdue domain → 建 `mail.mail_activity_data_todo` 活動（摘要「逾期租金待處理」，同人同期次不重複），指派給物件 `manager_id` → 租約建立者 → 目前使用者。

### ggandy.maintenance.request（報修單）

狀態：`new → assigned → in_progress → waiting → done`，任意 → `cancelled`，`→ new`（reopen）。

- `action_assign` 需要內部負責人或廠商；`action_done` 需要 `resolution`，並寫入 `completed_date`。
- `charged_to`：company / owner / tenant / pending。目前**不會**自動產生帳單，也不進帳務總表。

### account.move（inherit）

| 欄位 | 用途 | 誰寫入 |
| --- | --- | --- |
| `ggandy_lease_id` | 房客租金 Invoice → 租約 | 租金期次建帳單 |
| `ggandy_property_id` / `ggandy_unit_id` | related 自 lease（stored） | 自動 |
| `ggandy_owner_contract_id` | 房東 Vendor Bill → 房東合約 | 房東結算 cron |
| `ggandy_settlement_period_start/end` | 房東結算月份 | 房東結算 cron |
| `ggandy_expense_property_id` / `ggandy_expense_unit_id` / `ggandy_expense_kind` | 採購費用 / 員工代墊歸屬 | 人工（見 TODO：目前 vendor bill 表單看不到） |

### ggandy.accounting.overview（帳務總表）

- 一列 = 一個出租單位 × 一個月（`unique(unit_id, period_start)`）。
- `ensure_period_records()`：替所有 active 單位建立當月列，並刪除當月 `unit_id = False` 的舊格式列。
- 所有金額非 stored，每次開啟即時計算：

```text
rent_receivable  = Σ 本單位當月（period_start 在月內）期次 total_amount
rent_collected   = Σ 這些期次 invoice 的已付金額（已過帳）
rent_uncollected = max(receivable − collected, 0)
tenant_deposit   = Σ 本單位當月開始、state ∈ {active, expired, terminated} 的租約押金
ratio            = 本單位 monthly_rent / 物件所有 active 單位 monthly_rent 合計（合計為 0 → 1/單位數）
owner_payable    = Σ 當期房東合約當月 Vendor Bill（含 refund 為負）× ratio
owner_paid       = Σ 已付金額 × ratio
purchase_cost    = Σ 歸屬本物件+本單位、kind=purchase、invoice_date 在月內的 vendor bill
employee_advance = 同上，kind=employee_advance
net_cash_flow    = collected + deposit − owner_paid − purchase_cost − employee_advance
```

### Google Sheet 匯入（TransientModel）

- 精靈開啟時的 `sheet_url` 預設值由 `ggandy.google.sheet.import.mixin._default_sheet_url()` 讀系統參數 `ggandy_property_management.property_sheet_url` / `contact_sheet_url`，留空退回程式內的 `DEFAULT_*_SHEET_URL`。
- `ggandy.google.sheet.property.import.wizard`：Google Sheet URL → `/export?format=csv&gid=...` → `pandas.read_csv(dtype=str)`。
  - 必要欄位：`編號`、`案件名稱`；選填：`經營模式`（包租／代管／混合）、`房東`、`地址`、`管理人員`、`匯款日期`（取第一組數字，1–31）、`出租單位`（以 `,`／`，`／`、` 分隔）。標題會先 strip。
  - Upsert 鍵：`ir.model.data`，`module=ggandy_property_management`、`name=google_sheet_property_<編號slug>_<sha1(編號|案件名稱)[:10]>`。
  - 更新時一律覆寫 name、note；其他欄位**只有 Sheet 有值才寫入**。新建時經營模式留空用 wizard 的 `management_mode`，房東／管理人員留空就真的留空（`ggandy.property.owner_id` 已非必填）。
  - 房東：`res.partner` 依 `name =` 比對，同名優先 `is_ggandy_owner`；找不到且 `create_missing_owner` 時自動建立（`is_ggandy_owner=True`）。
  - 管理人員：`res.users` 依 `name` 或 `login` 比對，限 `share = False`（內部使用者）；**不自動建立**，找不到就留空，並收集到 `manager_warnings`，在結果最上方以 `alert-warning` 列出「編號／案件名稱：管理人員「X」不是內部使用者」。
  - 出租單位：只建立物件底下（含封存）沒有的名稱；`\d{3,4}` 房號自動帶 floor（去掉末兩碼），名稱含「店面」「車位」設 unit_type。
- 共用 `ggandy.google.sheet.import.mixin`（AbstractModel）：`result_html`、`_read_google_sheet_csv`（會 strip 表頭）、`_to_google_sheet_csv_url`、`_check_required_columns`、`_render_result_html`（警告框 + 摘要 + 前 80 筆訊息）、`_reopen_wizard`、`_clean_cell`。
- `ggandy.google.sheet.contact.import.wizard`：預設 URL `DEFAULT_CONTACT_SHEET_URL`（與物件同一份試算表的 `gid=0`）。表頭 `partner_key,name,phone,email,street,city,zip,is_owner,is_tenant,is_vendor`，僅 `name` 必填；`mobile` 已停用（`DEPRECATED_CONTACT_COLUMNS`，出現時只提醒不匯入）。
  - 對應既有聯絡人：`ref = partner_key` → `email =ilike` → 電話（SQL `regexp_replace(phone, '\D', '', 'g')` 只比數字）→ `name =`。有 partner_key 但 ref 找不到時，後三種只在 `ref` 空白的聯絡人中找，避免合併不同 key 的人。同一條件 >1 位 → 整列略過、列入警告。
  - 寫入：留空不覆蓋。
  - 身分旗標：`TRUE/1/y/yes/v/是/✓` → 設 True；`FALSE/0/n/no/否/空白` → 不變更；其他值 → 訊息提醒。只加不減，避免把物件／合約在用的房東身分拿掉。
- 外部腳本 `scripts/google_res_partner.py`（舊版，已由上面的精靈取代）：XML-RPC 匯入聯絡人，依 ref → email → mobile → phone → name 找既有聯絡人。注意它會把 FALSE 寫回身分旗標，且 Odoo 19 沒有 `mobile` 欄位，手機會被略過。

- `ggandy.google.sheet.lease.import.wizard`（`models/google_sheet_lease_import_wizard.py`）：三個 URL 欄位（`lease_sheet_url` / `meter_sheet_url` / `adjustment_sheet_url`，預設值讀系統參數 `ggandy_property_management.lease_sheet_url` / `meter_sheet_url` / `adjustment_sheet_url`），依序處理：
  - **房間租約**：`物件編號 → ggandy.property.code`、`房號 → unit.name`。先 `_write_changed(unit, 水電設定)`（只寫有變的欄位，避免 `_sync_utility_settings_to_schedules` 把期次上的調整蓋回預設），再以 `unit_id + start_date + state != cancelled` 找租約：沒有就建立草稿；草稿就更新有變的欄位（月租變了會重算未開帳期次的租金、入住度數變了會改第一期上期度數）；非草稿只警告不改。最後 `_generate_rent_schedule()` 補期次（草稿租約也會產生）。房客：電話（共用聯絡人精靈的 `_partner_ids_by_phone_digits`）→ 名稱，多筆就略過並警告。
  - **抄表度數**：依（物件、房號、月份）排序後寫入，`READING_TO_BILL_MONTH_OFFSET = 1`：M 月讀數 → M+1 月期次的 `*_curr_reading`，靠期次 `write` 的 `_propagate_meter_reading` 往後帶上期度數。非依度數計費、已過帳、讀數倒退 → 警告略過。
  - **帳單調整**：找該月期次，寫 `rent_amount`、`water_fixed_fee`（僅固定水費）、`note`；已有帳單就不改。`電費（核對用）` 只和 `electricity_amount` 比對。
  - 最後 `_check_suspicious_electricity`：期次電費 > 月租就警告（抓入住度數填 0 這種錯）。
  - **預覽**：在 `cr.savepoint()` 裡跑完整流程 → `flush_all()` → 丟 `_PreviewRollback` 還原 → `invalidate_all()`；預覽建立的租約用 `（預覽）…` 名稱，不取正式編號。每列寫入也包 savepoint，單列失敗只記警告。
- 測試資料重置：`notebook/scripts/reset_property_tenants.py`（預設 `PROP/2026/0134`、只刪 `demo-` 開頭房客，`COMMIT=1` 才寫入）。

### 包租獲利試算（TransientModel）

- `ggandy.master.lease.profit.wizard`（`models/master_lease_profit_wizard.py`），選單只開給 `group_property_manager`。
- 只有「關閉」按鈕，結果全是非 stored compute，在畫面上即時重算，不會寫入資料庫。`property_id` 只用來帶入 `room_count`（= `unit_count`）。
- 公式（N=房間數、P=每間付房東、R=每間月租、o=出租率、T=包租月數、F=免租裝潢月數、I=初始投入）：

```text
monthly_owner_total    = N × P
monthly_income         = N × R × o
monthly_profit         = monthly_income − monthly_owner_total
total_profit           = monthly_profit × T − I
roi                    = total_profit / I
annual_roi             = roi / ((F + T) / 12)          # 免租期也算資金卡住的時間
payback_months         = F + I / monthly_profit        # total_profit < 0 → 包租期內無法回本
break_even_occupancy   = (N×P×T + I) / (N×R×T)
break_even_tenant_rent = (N×P×T + I) / (N×o×T)
max_owner_rent         = (N×R×o×T − I) / (N×T)
```

- 免租裝潢期不付房東、也沒有收入，所以不影響總賺賠，只影響回本月數與年化報酬率。

### 設定頁（`res.config.settings`）

主模組建立 `<app name="ggandy_property_management" string="包租代管">`（view `res_config_settings_view_form_ggandy`，action `action_ggandy_settings`，選單「整合工具 → 設定」），區塊順序：

| 區塊 | 來源模組 | 內容 |
| --- | --- | --- |
| 擴充模組 `ggandy_modules` | 主模組 | `module_ggandy_property_telegram`、`module_ggandy_property_project`（Odoo 原生 `module_*`：勾選安裝、取消解除安裝） |
| Telegram Bot | telegram（xpath `ggandy_modules` after） | token / username / polling（`ggandy_property_telegram.*` 系統參數） |
| 整備專案 | project（xpath `ggandy_google_sheet` before） | `action_open_ggandy_preparation_template()` 開範本任務 |
| Google Sheet 匯入 `ggandy_google_sheet` | 主模組 | `ggandy_property_sheet_url`、`ggandy_contact_sheet_url`（`ggandy_property_management.*` 系統參數） |

擴充模組新增設定時，繼承 `ggandy_property_management.res_config_settings_view_form_ggandy` 插 block，不要另開 `<app>`。

## 3. Telegram 模組

```text
ir.cron（每分鐘）→ ggandy.telegram.log._cron_poll_updates
   → TelegramAPIClient.get_updates(offset)        # offset 存在 ir.config_parameter
   → _process_update
        非私訊 → log ignored
        _find_authorized_user(telegram_user_id)  # telegram_enabled + group_property_manager
           未授權 → 回覆 User ID，log denied
        /status /overdue /renew(/leases) /maintenance /start /help → _build_*_message(user)
        /renew [天數]：end_date ∈ [today, today + 1 個月 或 N 天] 的 active 租約 + end_date < today 仍 active 的租約
   → _send_and_log → TelegramAPIClient.send_message
```

`ir.config_parameter` keys（前綴 `ggandy_property_telegram.`）：`bot_token`、`bot_username`、`polling_enabled`、`update_offset`、`overdue_digest_sent_date`。

## 4. 整備專案模組（`ggandy_property_project`）

depends：`ggandy_property_management`、`sale_project`、`project_purchase`、`project_account`、`project_purchase_stock`（帶入 `stock`；主模組不依賴庫存）。不新增模型，只 inherit `ggandy.property`、`ggandy.property.unit`、`res.config.settings`。

| 欄位 / 方法 | 說明 |
| --- | --- |
| `preparation_project_id` | 整備專案，`copy=False`、readonly；`models.Constraint unique(preparation_project_id)` |
| `preparation_task_count` | 專案 `open_task_count`（compute_sudo），給智慧按鈕 |
| `action_start_preparation()` | 冪等：已有專案就直接開啟。檢查 manager 群組、`management_mode == master_lease`、`owner_id` → 以 sudo 從範本建立專案 → 補建分析帳戶 → 回寫物件 |
| `stock_location_id`（物件、出租單位） | 對應的 `stock.location`，`copy=False`、readonly、`ondelete=set null` |
| `_ensure_preparation_sale_order(project)` | 找 `project_id`＋`partner_id = owner_id`＋未取消的銷售單；沒有就 sudo 建空單並 `action_confirm()` |
| `_ensure_stock_locations()` | 冪等。公司第一個倉庫的 `lot_stock_id` → `{物件名稱}` → 每個 active 單位 `{單位名稱}`；已連結就跳過，否則同上層同名的 internal 位置沿用，再沒有才建立；回傳（新建數, 沿用數） |
| `action_sync_stock_locations()` | 包租且已有整備專案；manager 才可按；呼叫 `_ensure_stock_locations()` 並跳通知 |
| `action_view_preparation_project()` | 回傳專案的 `action_view_tasks()` |
| `action_create_preparation_purchase()` | 包租且已有整備專案才可用；開新的 `purchase.order` form，context `default_project_id` = 整備專案（與 Odoo `project_purchase` 的 `action_open_project_purchase_orders` 同做法） |

```text
物件「開始整備」
  → env.ref("ggandy_property_project.project_preparation_template")
       is_template → action_create_from_template({name: 整備-{物件}, partner_id: 房東, allow_billable, company_id, user_id: 管理人員, date_start})
       範本不存在 → project.project.create(只帶 7 個預設階段)
  → project.account_id 為空 → project._create_analytic_account()   # account_id copy=False，範本複製不會帶
  → property.preparation_project_id = project
  → _ensure_preparation_sale_order(project)    # 空銷售單，已確認（重新開立發票成本需要 state = sale）
  → _ensure_stock_locations()                  # WH/庫存/{物件}/{單位}

sale.order / purchase.order 的 project_id = 整備專案
  → 明細 _compute_analytic_distribution 帶入 project._get_analytic_distribution()
  → 帳單明細帶分析 → 專案獲利能力 / 分析報表
```

- `data/project_preparation_data.xml` 為 `noupdate="1"`：範本專案先建，任務階段再用 `project_ids` 掛到範本上（沒有專案的階段會被 `project.task.type._default_user_id` 當成個人階段），最後是 12 個範本任務。
- 使用者在 UI 改範本任務，升級模組不會覆蓋。
- 單位改名不會改位置名稱；封存的單位不建位置；刪除單位不刪位置（位置可能有庫存）。
- 廠商帳單「重新開立發票成本」轉到銷售單的明細（`is_expense`）若是商品類，`sale_stock` 會照常產生交貨單；目前由使用者手動取消（從交貨單轉單的流程 Odoo 有 `skip_procurement`，帳單流程沒有）。

## 5. 排程總表

| Cron 名稱 | Model.method | 頻率 |
| --- | --- | --- |
| GGAndy：自動建立租金帳單 | `ggandy.rent.schedule._cron_create_due_invoices` | 1 天 |
| GGAndy：建立逾期租金提醒 | `ggandy.rent.schedule._cron_create_overdue_rent_activities` | 1 天 |
| GGAndy：自動建立房東 Vendor Bill | `ggandy.owner.contract._cron_create_owner_vendor_bills` | 1 天 |
| GGAndy：更新帳務總表月份 | `ggandy.accounting.overview._cron_ensure_current_period_records` | 1 天 |
| GGAndy Telegram：接收管理員指令 | `ggandy.telegram.log._cron_poll_updates` | 1 分鐘 |
| GGAndy Telegram：逾期租金摘要 | `ggandy.telegram.log._cron_send_overdue_rent_digest` | 1 天 |

`odoo.conf`：`workers = 0`、`max_cron_threads = 1`，所有 cron 共用一條 thread。

## 6. 編號規則與產品

| Sequence code | 前綴 | 例 |
| --- | --- | --- |
| `ggandy.property` | `PROP/%(year)s/` | PROP/2026/0001 |
| `ggandy.lease` | `LEASE/%(year)s/` | LEASE/2026/00001 |
| `ggandy.owner.contract` | `OWNER/%(year)s/` | OWNER/2026/00001 |
| `ggandy.maintenance.request` | `REPAIR/%(year)s/` | REPAIR/2026/00001 |

| 產品 xmlid | 名稱 | 用途 |
| --- | --- | --- |
| `product_product_rent` | 房屋租金（GGANDY-RENT） | 房客 Invoice 租金行 |
| `product_product_management_fee` | 租屋管理費（GGANDY-MGMT-FEE） | 房客 Invoice 管理費行 |
| `product_product_electricity` | 電費（GGANDY-ELECTRICITY） | 房客 Invoice 電費行（不帶銷項稅） |
| `product_product_water` | 水費（GGANDY-WATER） | 房客 Invoice 水費行（不帶銷項稅） |
| `product_product_owner_settlement` | 房東結算款（GGANDY-OWNER-SETTLEMENT） | 房東 Vendor Bill |
