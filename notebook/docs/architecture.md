# GGAndy 系統架構與資料模型

> 對應程式碼版本：2026-10（`ggandy_property_management` 19.0.1.0.0、`ggandy_property_telegram` 19.0.1.0.1）。
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

`_generate_rent_schedule()` 拆月規則：

```text
for 每個月 month_cursor（start_date 月 ~ end_date 月）:
    period_start = max(start_date, 月初)
    period_end   = min(end_date, 月底)
    due_date     = 該月 rent_due_day（短月取月底），若早於 start_date 則用 start_date
    同 lease + period_start 已存在 → 跳過（可重複執行）
```

> 注意：期次金額**不按天數比例**，月中入住 / 退租的那一期也是完整月租。

### ggandy.rent.schedule（租金期次）

| 欄位 | 說明 |
| --- | --- |
| `total_amount` | stored compute = `rent_amount + management_fee` |
| `invoice_id` | 建立的客戶發票（租金、管理費兩行） |
| `collection_state` | 非 stored compute：uninvoiced / draft / unpaid / partial / paid / overdue / cancelled |

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

- `ggandy.google.sheet.property.import.wizard`：Google Sheet URL → `/export?format=csv&gid=...` → `pandas.read_csv(dtype=str)`。
  - 必要欄位：`編號`、`案件名稱`；選填：`地址`、`匯款日期`（取第一組數字，1–31）。
  - Upsert 鍵：`ir.model.data`，`module=ggandy_property_management`、`name=google_sheet_property_<編號slug>_<sha1(編號|案件名稱)[:10]>`。
  - 更新時會覆寫 name、owner、manager、management_mode、note（以及有值的 street、owner_payment_day）。
- `ggandy.google.sheet.contact.import.wizard`：尚未實作（`UserError`）。
- 外部腳本 `scripts/google_res_partner.py`：XML-RPC 匯入聯絡人，欄位 `partner_key,name,phone,mobile,email,street,city,zip,is_owner,is_tenant,is_vendor`，依 ref → email → mobile → phone → name 找既有聯絡人。

## 3. Telegram 模組

```text
ir.cron（每分鐘）→ ggandy.telegram.log._cron_poll_updates
   → TelegramAPIClient.get_updates(offset)        # offset 存在 ir.config_parameter
   → _process_update
        非私訊 → log ignored
        _find_authorized_user(telegram_user_id)  # telegram_enabled + group_property_manager
           未授權 → 回覆 User ID，log denied
        /status /overdue /leases /maintenance /start /help → _build_*_message(user)
   → _send_and_log → TelegramAPIClient.send_message
```

`ir.config_parameter` keys（前綴 `ggandy_property_telegram.`）：`bot_token`、`bot_username`、`polling_enabled`、`update_offset`、`overdue_digest_sent_date`。

## 4. 排程總表

| Cron 名稱 | Model.method | 頻率 |
| --- | --- | --- |
| GGAndy：自動建立租金帳單 | `ggandy.rent.schedule._cron_create_due_invoices` | 1 天 |
| GGAndy：建立逾期租金提醒 | `ggandy.rent.schedule._cron_create_overdue_rent_activities` | 1 天 |
| GGAndy：自動建立房東 Vendor Bill | `ggandy.owner.contract._cron_create_owner_vendor_bills` | 1 天 |
| GGAndy：更新帳務總表月份 | `ggandy.accounting.overview._cron_ensure_current_period_records` | 1 天 |
| GGAndy Telegram：接收管理員指令 | `ggandy.telegram.log._cron_poll_updates` | 1 分鐘 |
| GGAndy Telegram：逾期租金摘要 | `ggandy.telegram.log._cron_send_overdue_rent_digest` | 1 天 |

`odoo.conf`：`workers = 0`、`max_cron_threads = 1`，所有 cron 共用一條 thread。

## 5. 編號規則與產品

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
| `product_product_owner_settlement` | 房東結算款（GGANDY-OWNER-SETTLEMENT） | 房東 Vendor Bill |
