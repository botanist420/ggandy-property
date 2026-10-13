# GGAndy 系統架構與資料模型

> 對應程式碼版本：2026-10（`ggandy_property_management` 19.0.1.5.0、`ggandy_property_telegram` 19.0.1.0.2、`ggandy_property_project` 19.0.1.1.0）。
> 程式有改動時請同步更新本文件。

## 1. 模型關係圖

```text
                         res.partner
          (is_ggandy_owner / is_ggandy_tenant / is_ggandy_vendor)
             ▲ owner_id          ▲ tenant_id            ▲ vendor_id
             │                   │ co_tenant_ids(M2M)   │
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
| `management_mode` | master_lease（包租）/ agency（代管）（2026-10 取消 mixed） |
| `owner_id` | 房東，domain `is_ggandy_owner = True`，`ondelete=restrict`（2026-10 取消共同屋主 `co_owner_ids`，舊 rel table `ggandy_property_co_owner_rel` 留在 DB 但不再使用） |
| `manager_id` | 內部管理人員，逾期活動優先指派給他 |
| `owner_payment_day` | 匯款給房東的日期（1–31，0 = 未設定），房東合約 create / onchange 時預設帶入 |
| `acquisition_date` / `management_end_date` | 房東合約生效時回寫 |
| `has_open_owner_contract` | 非 stored compute：有 draft / active 房東合約即 True；False 時表單 header 顯示「新增房東合約」（`action_create_owner_contract`，與 `action_view_owner_contracts` 共用 `_get_owner_contract_default_context()`） |
| `occupied_unit_count` / `occupancy_rate` | 非 stored compute（`_compute_occupancy`）：房間 `state == occupied` 的數量，出租率＝已出租 ÷ 非停用房間（整數 %），給物件 kanban 卡片與列表用 |
| `analysis_html` | `models/property_analysis.py`：非 stored compute、`sanitize=False`，`_get_analysis_data()` 蒐集後用 QWeb `property_analysis`（`views/property_analysis_templates.xml`）渲染。期間 = 今天往前 365 天；租約只算 `EFFECTIVE_LEASE_STATES`；統計用標準庫 `statistics`（樣本標準差、`quantiles(method="inclusive")`、IQR 1.5 倍離群、|z| ≥ 1.5 標色）。付款延遲 = 應收分錄 `matched_credit_ids.max_date` − `due_date`；準時率 = 準時付清 ÷（已付清＋目前逾期）；時間加權出租率 = 各房間有效租約覆蓋天數（合併重疊、提前終止用 `termination_date`）÷（非停用房間數 × 365）；月度趨勢 `_read_group` 帳務總表；包租價差用 `_get_month_owner_contract()` 的 `guaranteed_rent` 。分頁標題暫名「Lucas統計」；名詞說明在 `GLOSSARY`（表頭 `title` 提示＋頁尾 `<details>` 名詞解釋） |
| `state_id` / `city_id` / `zip` / `city` | 地址：`city_id` 是 `res.city`（台灣資料來自 `l10n_tw` 的 `data/res.city.csv`，所以 depends `l10n_tw`），view domain 限定 `state_id`；onchange `city_id` 帶入 `city`（區名 Char）、`zip`、`state_id`，onchange `state_id` 時不符的 `city_id` 清空。`city` Char 保留給 `address_display`、租約 PDF `_format_premises` 用 |
| `current_schedule_ids` | One2many `ggandy.rent.schedule`（inverse `property_id`），callable domain `_get_current_schedule_domain()`：租約非 cancelled、`period_month` = 本月（每次讀取時計算）；表單「當期帳單」分頁 `readonly="1"`、`default_order="unit_id"`，只供檢視 |
| `internet_fee` | 網路第四台（Monetary，tracking），每月固定金額，算進本月收租概覽的合計並轉付給房東，不進房客帳單 |
| `month_*_total` / `month_summary_label` / `month_owner_payout` | 「本月收租概覽」：非 stored compute（`_compute_month_summary`）呼叫 `_get_month_summary(本月)`：搜尋該物件 `period_month` = 本月、租約狀態在 `EFFECTIVE_LEASE_STATES` 的收租明細，加總租金／管理費／電費／水費，加上 `internet_fee`。`month_owner_payout`「付房東」= 本月有效 active 房東合約（`_get_month_owner_contract`）的 `_get_owner_settlement_lines(..., summary)` 合計（傳入 in-memory summary，表單改網路第四台時即時更新）。順序照老闆付房東 Sheet（2026-10-11 驗算：Sheet 的 N 月 = N 月帳單 `period_month`） |
| `current_meter_schedule_ids` | 同上再加「電費 metered 或水費 metered／fixed」（`_get_current_meter_schedule_domain()`）；表單「水電表維護」分頁 `default_order="unit_id"`（照房間 `_order`），直接編輯收租明細的 `electricity_curr_reading`、`water_fixed_fee`（欄名「水費」，fixed 才顯示）與 `water_curr_reading`（`optional="hide"`，metered 才顯示），連動走收租明細 `write()` |

### ggandy.property.unit（房間）

| 欄位 | 說明 |
| --- | --- |
| `sequence` | `_order = "property_id, sequence, id"`；物件 form 房間分頁 `widget="handle"` 可拖曳。`create()` 沒帶 sequence 時接在同物件（含封存）最大值 +1，批次依序累加；既有資料升級時全為 10，靠 id 維持建立順序 |
| `unit_type` | suite / room / whole / shop / office / parking / other |
| `monthly_rent` | 參考月租：租約預設值，也是帳務總表房東應付的**分攤權重** |
| `deposit_months` | 押金月數（預設 2） |
| `electricity_billing_type` / `water_billing_type` | metered（依度數）/ fixed（每月固定）/ included（含租金）/ tenant_paid（房客自繳）；預設電費 metered、水費 fixed，空白 = 未設定 |
| `electricity_rate` / `water_rate` | 每度單價（Float, digits (10,2)，不用 Monetary 以免被幣別小數位四捨五入）；費用 = (本月度數 − 上月度數) × 單價 |
| `electricity_fixed_fee` / `water_fixed_fee` | 每月固定水電費（Monetary） |
| `electricity_meter_ref` / `water_meter_ref` | 電錶／水錶編號或位置 |
| `state` | vacant / reserved / occupied / maintenance / inactive |
| `meter_schedule_ids` | One2many `ggandy.rent.schedule`（inverse `unit_id`），domain 租約非 cancelled 且（電費 metered 或水費 metered／fixed），不限月份，欄位同物件的水電表維護分頁；表單分頁 `default_order="period_start desc"`；收租明細本來就依租約起訖拆月，所以列出的就是租約期間的所有月份 |
| `action_open_unit()` / `action_view_current_lease()` | 物件 form 房間分頁的「🏠房間」「📄租約帳單」按鈕（`icon` + `o_ggandy_icon_tight`，`static/src/scss/ggandy_backend.scss` 把 Odoo 預設的圖示右邊距拿掉）；租約挑選順序：active → draft → 其他（依 start_date 新到舊）→ cancelled，沒有租約時回傳 `action_view_leases()` |
| `has_open_lease` / `action_create_lease()` | 有 draft / active 租約（與 `lease_count` 同一個 compute）；表單 header 兩顆「新增房客租約」按鈕依它切換 `btn-primary` / `btn-secondary`，開新 `ggandy.lease` form 並帶 `default_*`（與 `action_view_leases()` 同一組） |
| `layout_photo_ids` | compute：本房間 chatter 上的 `image/*` 附件 |
| `maintenance_photo_ids` | compute：本房間所有報修單（含封存）的 `image/*` 附件，依 `ggandy_request_date` desc |
| `current_lease_id` / `current_tenant_id` / `current_lease_end_date` | 非 stored compute（`_compute_current_lease`）：今天落在租期內的生效租約與其房客、租期結束，給房間列表顯示 |

`_refresh_from_active_leases()`：只在 `vacant` / `occupied` 狀態下自動切換；有 active 租約**且 `start_date <= today <= end_date`** 才算 occupied（預約入住的租約生效後房間仍是空房）。每日 `_cron_expire_ended_leases()` 最後會對所有 vacant／occupied 房間跑一次。

### ggandy.owner.contract（房東合約）

狀態：`draft → active → expired / terminated`，`draft → cancelled`，`terminated / cancelled → draft`。

| 欄位 | 說明 |
| --- | --- |
| `owner_id` | 簽約房東，domain `is_ggandy_owner = True`（view context `default_is_ggandy_owner`，就地新建的聯絡人會勾好） |
| `contract_type` | master_lease（包租）/ agency（代管） |
| `owner_payment_day` | 房東匯款日＝每月結算日（1–31，預設 10，或從物件帶入） |
| `guaranteed_rent` | 包租保底租金（生效前必填 > 0） |
| `fee_type` / `fee_rate` / `fixed_fee` | 代管費：百分比（填 10 = 10%）或每月固定金額 |
| `owner_deposit` | 公司付給房東的押金（僅記錄，不進結算） |

**每月房東帳單明細** `_get_owner_settlement_lines(period_start, period_end, summary=None)` → `[(說明, 金額), ...]`，0 元不列：

```text
包租：「YYYY-MM 包租保底租金」 guaranteed_rent
代管：collected = Σ(收租明細 rent_amount × 該期 invoice 已付比例)
        （只算 period_start 落在當月、invoice 已過帳的收租明細；不含管理費）
      fee = collected × fee_rate / 100        （percentage）
          = fixed_fee if collected else 0     （fixed）
      「YYYY-MM 代管實收租金扣代管費」 max(collected − fee, 0)
＋ 代收轉付（property._get_month_summary(period_start)，和物件收租概覽同一個計算）：
      管理費、網路第四台、電費、水費（各一行，名稱帶「（代收轉付）」）
```

`_cron_create_owner_vendor_bills`：今天 ≥ 當月結算日（短月取月底）且合約期間涵蓋當月 → `_create_owner_vendor_bill()`：同合約同 `ggandy_settlement_period_start` 已有非取消帳單則跳過；合計 ≤ 0 不建立；否則 `_create_settlement_bill(period_start, period_end, lines)` 建 `in_invoice`，每行都用房東結算產品（未稅），`name` 是說明（本身不做重複檢查，一律透過 `_create_owner_vendor_bill` 呼叫）。

**一個月一張房東帳單**：`_create_owner_vendor_bill()` 同合約同月份已有非取消帳單就直接回傳那張。合約 header「開立本月房東帳單」→ `action_create_current_month_bill()`（active、合約期間涵蓋本月、`groups=account.group_account_invoice`）走同一個方法，回傳帳單 form；之後排程到結算日會看到已有帳單而略過。

**補開歷史房東帳單**：`_get_missing_bill_periods(today=None)` 從合約開始月逐月往後，取結算日（`_get_settlement_date()`：`owner_payment_day`，短月取月底）≤ 今天、且不超過合約結束的月份，排除已有非取消帳單與應付 ≤ 0 的月份。`action_activate()` 單筆生效後有缺月份就回傳 `action_open_bill_backfill_wizard()`；合約 header「補開歷史房東帳單」用非儲存 compute `has_missing_past_bills` 控制顯示（`groups=account.group_account_invoice`）。精靈 `ggandy.owner.bill.backfill.wizard`（＋ `.line`，`models/owner_bill_backfill_wizard.py`）在 `default_get` 列出缺的月份與試算明細，`action_create_bills()` 對勾選月份呼叫 `_create_owner_vendor_bill(start, end, invoice_date=該月結算日)`，一律草稿；明細欄位 readonly 所以 view 要 `force_save="1"`。明細分欄來自 `_get_owner_settlement_breakdown()`（保底／代管款、管理費、網路第四台、電費、水費、調整，含 0 元）；`_get_owner_settlement_lines()` 由它組出帳單明細，順序相同、0 元不列。精靈「退回草稿」→ `_revert_to_draft()`（限 active 且沒有未取消房東帳單），回傳 `soft_reload`。物件「新增房東合約」帶 `ggandy_from_property`，合約 form 的 `property_id` 一律鎖、`contract_type`／`owner_id` 在 context 有對應 default 時才鎖（都 `force_save`），`guaranteed_rent` 有 `default_focus`。`_create_owner_vendor_bill` / `_create_settlement_bill` 的 `invoice_date` 預設今天，cron 行為不變。

**重新計算房東帳單**（包租、代管都適用，依帳單月份）：Vendor Bill header「重新計算房東帳單」（有 `ggandy_owner_contract_id` + `ggandy_settlement_period_start`、未取消才顯示）→ `account.move.action_ggandy_recompute_owner_bill()` → `contract._recompute_settlement_bill(bill)` 回傳訊息：draft → product lines 整批 unlink 後換成新明細（合計 ≤ 0 `UserError`）；posted → 不改帳單、不開新帳單，只回報重算合計與差額（sticky warning 通知）。

**帳單調整** `ggandy.owner.adjustment`（`models/owner_adjustment.py`）：`contract_id`（cascade）、`period_start`（create/write 一律存成該月 1 號）、`period_month`（stored compute＋inverse，畫面上輸入「2026-09」「2026/9」，create/write 先換成 `period_start` 再由 compute 統一格式）、`name`、`amount`（可負）、`note`；`property_id` / `company_id` / `currency_id` related stored，有多公司 rule。合約 form「帳單調整」分頁（editable list）。`_get_owner_settlement_lines()` 最後加上 `period_start` 相同的調整，每筆一行「YYYY-MM 調整：{name}」。
調整和帳單明細行用 `account.move.line.ggandy_owner_adjustment_id`（set null）連起來：`_get_owner_settlement_line_specs()` 回傳 `(說明, 金額, 調整項)`，`_prepare_settlement_line_commands()` 建行時寫入連結，所以建帳單、重新計算（草稿整批換明細）都會自動連上。調整的 `is_applied` / `bill_id` 是 stored compute（依 `bill_line_ids.parent_state`，取消的帳單不算）。合約「插入帳單」→ `action_apply_adjustments_to_bills()`：對 `_get_pending_adjustments()`（未加入且該月已有未取消帳單）逐筆 `_add_to_bill()`，草稿帳單加一行（帳單上已有同說明同金額、沒連結的舊行就直接連上），已過帳略過並 sticky 提醒。已加入的調整 `write` 鎖定 period/name/amount、`unlink` 擋下；view 以 `decoration-muted` + `readonly="is_applied"` 呈現。

**匯款日同步**：`property.write()` 改 `owner_payment_day`（非空）→ `_sync_owner_payment_day_to_contracts()` 更新該物件 active 房東合約的 `owner_payment_day` 並 `message_post`。

`_onchange_property_id()`：帶 owner、company、owner_payment_day、contract_type，以及 start_date ← `acquisition_date`、end_date ← `management_end_date`（只在空白時）；物件的房東合約 smart button context 也帶 `default_start_date` / `default_end_date`。

### ggandy.lease（房客租約）

狀態：`draft → active → expired / terminated`，`draft → cancelled`，`→ draft`（無已過帳帳單才可），`expired → active`（續約）。

- **生效**：`action_activate()` 改 active → `_generate_rent_schedule()` → `_refresh_unit_state()` → `self.schedule_ids._create_due_invoices()`（只開這份租約的帳單，不再呼叫全域 cron）。
- **提前終止**：`action_open_terminate_wizard()` → `ggandy.lease.terminate.wizard`（`models/lease_terminate_wizard.py`，預設終止日 = 今天，顯示會刪除的收租明細數）→ `action_terminate(termination_date)`：只接受 active、終止日須在租期內；刪除 `period_start > 終止日` 且沒有 `invoice_id` 的收租明細，寫 `state=terminated`、`termination_date`，`message_post`，`_refresh_unit_state()`。終止日所在收租明細不重算。
- **自動到期**：`_cron_expire_ended_leases()`（每日）把 `active` 且 `end_date < today` 的租約改 `expired`、`message_post`，最後對所有 vacant／occupied 房間 `_refresh_from_active_leases()`。因此「已到期」的租約仍要參與收款與抄表：`_get_overdue_domain`、`_cron_create_due_invoices`、收租明細搜尋的逾期／待抄表篩選用 `state in (active, expired)`；`_find_unit_month_schedule`、`_sync_utility_settings_to_schedules` 用 `(draft, active, expired)`。
- **續約**：`action_open_renew_wizard()` → `ggandy.lease.renew.wizard`（`models/lease_renew_wizard.py`，TransientModel）。延長同一份租約，不另建租約：檢查新結束日 > 舊結束日、同房間沒有 draft/active 租約起租日落在 `(舊結束日, 新結束日]` → `write(end_date, rent_amount, management_fee[, state=active])` → `_refresh_unit_state()` → `auto_generate_schedule` 時 `_generate_rent_schedule(from_date=舊結束日 + 1)` → `message_post` → `lease.schedule_ids._create_due_invoices()`。三個預設值各自一個 compute（store、readonly=False）：共用同一個 compute 時，create 只帶其中一個，其他的就不會被算出來。
- **待續約**：`has_next_lease`（非 stored，`_search_has_next_lease` 用 SQL `EXISTS`：同 `unit_id`、`active`、`state in (draft, active)`、`start_date > 本租約 end_date`；Odoo 19 boolean 搜尋會正規化成 `in` / `not in`）、`days_to_end`（非 stored）、`tenant_phone`（related）。`action_ggandy_lease_renewal` domain `state in (active, expired) and has_next_lease = False`，預設 `search_default_ending_30`；list `view_ggandy_lease_renewal_list`（priority 20，不影響「房客租約」選單）。Telegram `/renew`、`/status` 的到期數也排除 `has_next_lease`。

房客：`tenant_id`、`co_tenant_ids` domain 都是 `is_ggandy_tenant = True`（view context `default_is_ggandy_tenant`，就地新建的聯絡人會勾好）。

水電設定（2026-10-12 起存在租約上，收費與合約 PDF 都照租約）：`{electricity,water}_billing_type` / `_rate` / `_fixed_fee` 為一般儲存欄位；`create()` 沒帶時、`_onchange_unit_id` 時從 `unit._get_utility_settings()` 複製。`write()` 改這些欄位 → `_sync_utility_settings_to_schedules()` 更新這份租約未過帳（無帳單或草稿帳單）的收租明細。`_generate_rent_schedule()` 用 `lease._get_utility_schedule_values(ratio)`（固定費按日比例）。既有租約由 `migrations/19.0.1.9.0` 用 SQL 從房間補值。`electricity_start_reading` / `water_start_reading` 為入住度數，onchange 房間時預設帶 `unit._get_last_meter_reading()`（該房間跨租約最近一筆 curr ≠ 0 的收租明細）。

`_generate_rent_schedule()` 拆月規則：

```text
first_date = max(start_date, from_date)   # from_date 預設 None = start_date；續約傳舊結束日隔天
for 每個月 month_cursor（first_date 月 ~ end_date 月）:
    period_start = max(first_date, 月初)
    period_end   = min(end_date, 月底)
    due_date     = 該月 rent_due_day（短月取月底），若早於 first_date 則用 first_date
    ratio        = 收租明細天數 ÷ 當月實際天數（整月 = 1）
    rent_amount / management_fee = 月額 × ratio，四捨五入到元（整月直接用月額）
    水電設定 = 房間快照（固定水電費同樣 × ratio）；prev_reading = _get_meter_reading_before()
    同 lease + period_start 已存在 → 跳過（可重複執行）
```

> 按日比例用**當月實際天數**（不是固定 30 天）：一年期租約起訖在同一個月份，第一期 + 最後一期剛好等於一個月，總額 = 12 個月租金。
> 例：9/25 起租、月租 31,000 → 第一期 9/25–9/30 = 31,000 × 6/30 = 6,200。
> 規則只影響新產生的收租明細；改版前已建立的收租明細維持原金額。
> 續約時舊結束日不在月底（例如 11/14），當月會拆成兩期：11/1–11/14（舊月租）與 11/15–11/30（新月租），各自按日比例。

`contract_document_ids`：非 stored compute，抓 `res_model = ggandy.lease`、`res_id = 本租約` 的所有 `ir.attachment`（不限圖片），一次 search 後依 `res_id` 分配；未儲存的新租約為空。

#### 租約 PDF（`report/lease_contract_report.*`）

- `ir.actions.report` `action_report_ggandy_lease_contract`（qweb-pdf，綁在租約的「列印」選單，表單 header 也有按鈕），紙張用自訂的 `paperformat_ggandy_lease_contract`（A4、四邊 18mm，因為 `web.basic_layout` 沒有頁首）。
- 範本是公司正式的「房屋租賃契約」（2026-10-09 依 R2 紙本合約改寫，取代原本的簡單版），固定四頁：①當事人資料＋第一、二條（租期、地址範圍、租金押金、水電、繳款帳戶）②第三、四條（含設備點交表）③第五～十條 ④《清潔標準》＋簽名與日期。條文是固定文字寫在 QWeb 裡。
- **不為合約新增欄位**：只帶系統裡既有的資料，其餘印空白底線讓房客／現場手寫（承租人的出生日、戶籍、公司、緊急聯絡人、分行、簽名日期、設備數量）。之後要記錄哪些欄位再另外討論。
- 資料由 AbstractModel `report.ggandy_property_management.report_lease_contract` 的 `_get_report_values` 準備：
  - 出租人判斷（`_get_contract_parties`）：找該物件在租約開始日有效的 active 房東合約 → 用 `contract_type`，找不到就用物件 `management_mode`。`master_lease` → 出租人 = 公司 partner、沒有丙方；其他（代管）→ 出租人 = 物件 `owner_id`、丙方 = 公司 partner。
  - 繳款帳戶：收款方（有丙方用丙方，否則出租人）的第一個 `bank_ids`；分行留空手寫。
  - 承租人資料：只印聯絡人既有的姓名、`vat`（身分證號）、`phone`（行動電話）、`email`，其餘欄位與兩位緊急聯絡人都印空白。
  - 地址範圍（`_format_premises`）= 物件縣市＋區＋street（已出現在 street 裡的不重複）＋房間樓層（`12F` → `12 樓`）。
  - 水電文字（`_get_utility_terms`）依房間的計費方式產生：metered 印每度單價與租約入住度數、fixed 印每月固定金額、included／tenant_paid 各有一句，未設定印空白底線。
  - 設備點交表：`EQUIPMENT_ROWS` 常數（合約範本的設備與賠償單價，每列 8 個），數量留空點交時手寫。
  - 條文中三處「乙方簽名＿＿ 年 月 日」與最後一頁日期都留空。「合約備註」印在第七條第 3 點。
- 字型用容器內的 `Noto Serif CJK TC`；分頁用 `page-break-before` 對齊紙本四頁。
- wkhtmltopdf（舊版 WebKit）的坑：資料表格的 td 不要加 `word-wrap: break-word`，colspan 裡的長文字會被算成兩行高、文字浮在底線上方；手寫欄位印 `nbsp` 讓底線列高度一致。
- 只下載、不自動存附件（範本沒設 `attachment`），避免每按一次就在 chatter 多一份。
- 預覽：可以在 Odoo shell 建一筆租約後 `env["ir.actions.report"]._render_qweb_pdf("ggandy_property_management.report_lease_contract", ids)` 寫檔再 `env.cr.rollback()`，不會留下資料（shell 用 `--no-http` 會有 wkhtmltopdf network warning，不影響 inline 樣式）。

### ggandy.rent.schedule（收租明細）

| 欄位 | 說明 |
| --- | --- |
| `{electricity,water}_billing_type` / `_rate` / `_fixed_fee` | 建收租明細時從**租約**快照（固定費按日比例）；租約改水電設定時更新該租約未過帳的收租明細。房間 `write()` 改設定 → `unit._sync_utility_settings_to_schedules()` 只寫到這個房間的**草稿**租約（再由租約同步） |
| `{electricity,water}_prev_reading` / `_curr_reading` | 上期／本期度數（Float digits (12,1)）。第一期 prev = 租約 `*_start_reading`，之後 = 前一期的 curr（沒抄就沿用前一期的 prev） |
| `{electricity,water}_usage` / `_amount` | stored compute：metered = (curr − prev) × rate 四捨五入到元（curr 為 0 視為未抄、金額 0）；fixed = fixed_fee；其他 = 0 |
| `period_start` / `period_end` / `due_date` | view 層 `readonly="id"`（租約內嵌 list 與收租明細 form）：已存檔的收租明細鎖住，新增中的列可填；model 層沒有限制，程式與匯入照常可寫 |
| `period_month` | 「帳單月份」，stored compute：`period_start` 的 `YYYY-MM`；物件的 `_get_current_schedule_domain()` 用它篩本月 |
| `meter_month` | 「電表月份」，同一個 compute：`period_start − READING_TO_BILL_MONTH_OFFSET` 個月的 `YYYY-MM`。水電表維護分頁、租約收租明細列表、收租明細 form 顯示它（`period_month` 在水電表維護分頁 `optional="hide"`） |
| `meter_reading_pending` | stored compute：任一項 metered 且 curr 為 0（「待抄表」篩選用） |
| `total_amount` | stored compute = `rent_amount + management_fee + electricity_amount + water_amount` |
| `invoice_id` | 建立的客戶發票（租金、管理費、電費、水費各一行，0 元不列） |
| `collection_state` | 非 stored compute：uninvoiced / draft / unpaid / partial / paid / overdue / cancelled |

- **度數連動**：`write()` 改 prev/curr 後呼叫 `_propagate_meter_reading()`，把讀數往後帶到同租約之後的收租明細，直到遇到下一筆已抄表（curr ≠ 0）的收租明細為止；已過帳的收租明細不動。租約改 `*_start_reading` 也用同一套（`_apply_previous_reading`）。
- **鎖定與同步**：帳單已過帳時改水電欄位 → `UserError`；帳單是草稿時 `_sync_draft_invoice_utility_lines()` 以產品比對更新／新增／刪除水電行。
- `@api.constrains`：curr 不可小於 prev（換錶要手動改 prev）。
- `_get_unpaid_domain()`、`_get_overdue_domain()`：共用的 domain，Telegram 也用。
- `_cron_create_due_invoices`：租約 active 或 expired 的收租明細 → `_create_due_invoices()`（recordset 方法：`invoice_id = False`、`period_start <= today` → `action_create_invoice()`，逐筆 try/except）。
- `EFFECTIVE_LEASE_STATES = (active, expired, terminated)` 與 `_get_effective_domain()`：會算進應收與收入的收租明細。物件「本月收租概覽」（`_get_month_summary`）、Telegram `/status`（依 `period_start` 月份）、帳務總表 SQL 都用這組狀態。
- **登記收款** `action_register_rent_payment()`（收租明細 list header，`groups=account.group_account_invoice`）：拒絕非有效租約的收租明細 → 沒開單的 `action_create_invoice()` → 草稿帳單 `action_post()` → 未付清（排除 paid / in_payment / reversed）的帳單 `action_register_payment()` 回傳原生 `account.payment.register` action。設計是「帳單平常維持草稿、收款時才過帳」。
- **逾期活動結案**：`account.move._invoice_paid_hook()`（Odoo 在帳單變 paid / in_payment 時呼叫）→ sudo 找對應收租明細 → `_close_overdue_activities()` 把摘要「逾期租金待處理」的活動 `action_feedback`。
- `_cron_create_overdue_rent_activities`：符合 overdue domain → 建 `mail.mail_activity_data_todo` 活動（摘要「逾期租金待處理」，同人同一個月份的收租明細不重複），指派給物件 `manager_id` → 租約建立者 → 目前使用者。

### ggandy.maintenance.request（報修單）

狀態：`new → assigned → in_progress → waiting → done`，任意 → `cancelled`，`→ new`（reopen）。

- `action_assign` 需要內部負責人或廠商；`action_done` 需要 `resolution`，並寫入 `completed_date`。
- `charged_to`：company / owner / tenant / pending。目前**不會**自動產生帳單，也不進帳務總表。

### account.move（inherit）

| 欄位 | 用途 | 誰寫入 |
| --- | --- | --- |
| `ggandy_lease_id` | 房客租金 Invoice → 租約 | 收租明細建帳單 |
| `ggandy_property_id` / `ggandy_unit_id` | related 自 lease（stored） | 自動 |
| `ggandy_owner_contract_id` | 房東 Vendor Bill → 房東合約 | 房東結算 cron |
| `ggandy_settlement_period_start/end` | 房東結算月份 | 房東結算 cron |
| `ggandy_expense_property_id` / `ggandy_expense_unit_id` / `ggandy_expense_kind` | 採購費用 / 員工代墊歸屬 | 人工（見 TODO：目前 vendor bill 表單看不到） |

### ggandy.accounting.overview（帳務總表）

- **`_auto = False` + `_table_query`**（2026-10-11 由「實體表 + 每日排程建列 + 非 stored compute」改寫）：沒有資料表、沒有 cron、不需要手動更新；所有欄位都是 SQL 欄位，所以 `read_group` 可以分組加總（list 依物件分組有小計），也能做樞紐分析／圖表。`init()` 只負責把改版前留下的實體表 `DROP TABLE ... CASCADE`。舊 cron 用 `data/accounting_cron.xml` 的 `<delete search=...>` 移除。
- 只呈現房客、房東的租金收付（2026-10-12 拿掉採購費用；整備與報修成本刻意不進總表），金額一律未稅。
- 列的範圍：每個 `active` 房間，從 `least(房間 create_date 月, 該房間有效租約（active/expired/terminated）最早 start_date 月)` 到 `CURRENT_DATE` 當月（`generate_series`）。封存的房間不出現。
- `id = unit_id * 2000 + (年 − 2000) * 12 + 月`（穩定、int4）。`_depends` 列出來源 model 欄位，同一交易內改到來源資料時 ORM 會失效快取。
- access 只讀（user / manager 都是 1,0,0,0），list / form `create="0" edit="0" delete="0"`。
- 計算（與改版前 Python 逐列比對 94 列完全一致）：

```text
rent_receivable  = Σ 本房間 period_start 在當月、租約 active/expired/terminated 的收租明細 total_amount
paid_ratio       = max(amount_total − amount_residual, 0) / amount_total（amount_total = 0 不算）
rent_collected   = Σ 這些收租明細 invoice：posted 才算，sign × amount_untaxed × paid_ratio，refund sign = −1
contract         = 物件當月有交集、state=active、start_date desc, id desc 的第一份房東合約
ratio            = 本房間 monthly_rent / 物件所有 active 房間 monthly_rent 合計（合計為 0 → 1/房間數）
owner_payable    = Σ 該合約、ggandy_settlement_period_start = 當月 1 號、in_invoice/in_refund、非 cancel 的 sign × amount_untaxed × ratio
owner_paid       = 同一批帳單 posted 的 amount_untaxed × paid_ratio × ratio
rent_uncollected = max(receivable − collected, 0)；owner_unpaid = max(payable − paid, 0)
expected_profit  = receivable − owner_payable（預計賺）
earned_profit    = collected − owner_paid（已經賺到）
has_*            = 上面數字的 SQL 布林欄位，原生可搜尋
```

- 欄位名稱：應付房東（`owner_payable`）、已付房東（`owner_paid`）、待付房東（`owner_unpaid`）。押金（`tenant_deposit`）、員工代墊（`employee_advance`）、備註（`note`）已移除；Vendor Bill 上的「公司員工代墊」費用類型仍可標記，只是不進總表。
- list 只在單一欄位上色：`rent_collected` warning（`rent_uncollected > 0`）、`owner_paid` warning（`owner_unpaid > 0`）、`earned_profit` danger（< 0）。action 預設 `search_default_current_month` + `search_default_group_property`。
- 帳務選單（2026-10-13 由「GGandy帳務」改名）：營運總表（10）、逾期未收（20，`ggandy.rent.schedule` + `search_default_overdue` + 依物件分組；收租明細 list 的金額欄都有 `sum`）、待付房東（30，`account.move` in_invoice 且有 `ggandy_owner_contract_id`，`search_default_open`，限 `account.group_account_invoice`）。

### Google Sheet 匯入（TransientModel）

- 精靈開啟時的 `sheet_url` 預設值由 `ggandy.google.sheet.import.mixin._default_sheet_url()` 讀系統參數 `ggandy_property_management.property_sheet_url` / `contact_sheet_url`，留空退回程式內的 `DEFAULT_*_SHEET_URL`。
- `ggandy.google.sheet.property.import.wizard`：Google Sheet URL → `/export?format=csv&gid=...` → `pandas.read_csv(dtype=str)`。
  - 必要欄位：`編號`、`案件名稱`；選填：`經營模式`（包租／代管）、`房東`、`地址`、`管理人員`、`匯款日期`（取第一組數字，1–31）、`出租單位`（以 `,`／`，`／`、` 分隔）。標題會先 strip。
  - Upsert 鍵：`ir.model.data`，`module=ggandy_property_management`、`name=google_sheet_property_<編號slug>_<sha1(編號|案件名稱)[:10]>`。
  - 更新時一律覆寫 name、note；其他欄位**只有 Sheet 有值才寫入**。新建時經營模式留空用 wizard 的 `management_mode`，房東／管理人員留空就真的留空（`ggandy.property.owner_id` 已非必填）。
  - 房東：`res.partner` 依 `name =` 比對，同名優先 `is_ggandy_owner`；找不到且 `create_missing_owner` 時自動建立（`is_ggandy_owner=True`）。
  - 管理人員：`res.users` 依 `name` 或 `login` 比對，限 `share = False`（內部使用者）；**不自動建立**，找不到就留空，並收集到 `manager_warnings`，在結果最上方以 `alert-warning` 列出「編號／案件名稱：管理人員「X」不是內部使用者」。
  - 房間：只建立物件底下（含封存）沒有的名稱；`\d{3,4}` 房號自動帶 floor（去掉末兩碼），名稱含「店面」「車位」設 unit_type。
- 共用 `ggandy.google.sheet.import.mixin`（AbstractModel）：`result_html`、`_read_google_sheet_csv`（會 strip 表頭）、`_to_google_sheet_csv_url`、`_check_required_columns`、`_render_result_html`（警告框 + 摘要 + 前 80 筆訊息）、`_reopen_wizard`、`_clean_cell`。
- `ggandy.google.sheet.contact.import.wizard`：預設 URL `DEFAULT_CONTACT_SHEET_URL`（與物件同一份試算表的 `gid=0`）。表頭 `partner_key,name,phone,email,street,city,zip,is_owner,is_tenant,is_vendor`，僅 `name` 必填；`mobile` 已停用（`DEPRECATED_CONTACT_COLUMNS`，出現時只提醒不匯入）。
  - 對應既有聯絡人：`ref = partner_key` → `email =ilike` → 電話（SQL `regexp_replace(phone, '\D', '', 'g')` 只比數字）→ `name =`。有 partner_key 但 ref 找不到時，後三種只在 `ref` 空白的聯絡人中找，避免合併不同 key 的人。同一條件 >1 位 → 整列略過、列入警告。
  - 寫入：留空不覆蓋。
  - 身分旗標：`TRUE/1/y/yes/v/是/✓` → 設 True；`FALSE/0/n/no/否/空白` → 不變更；其他值 → 訊息提醒。只加不減，避免把物件／合約在用的房東身分拿掉。
- 外部腳本 `scripts/google_res_partner.py`（舊版，已由上面的精靈取代）：XML-RPC 匯入聯絡人，依 ref → email → mobile → phone → name 找既有聯絡人。注意它會把 FALSE 寫回身分旗標，且 Odoo 19 沒有 `mobile` 欄位，手機會被略過。

- `ggandy.google.sheet.lease.import.wizard`（`models/google_sheet_lease_import_wizard.py`）：三個 URL 欄位（`lease_sheet_url` / `meter_sheet_url` / `adjustment_sheet_url`，預設值讀系統參數 `ggandy_property_management.lease_sheet_url` / `meter_sheet_url` / `adjustment_sheet_url`），依序處理：
  - **房間租約**：`物件編號 → ggandy.property.code`、`房號 → unit.name`。先 `_write_changed(unit, 水電設定)`（只寫有變的欄位，避免 `_sync_utility_settings_to_schedules` 把收租明細上的調整蓋回預設），再以 `unit_id + start_date + state != cancelled` 找租約：沒有就建立草稿；草稿就更新有變的欄位（月租變了會重算未開帳收租明細的租金、入住度數變了會改第一期上期度數）；非草稿只警告不改。最後 `_generate_rent_schedule()` 補收租明細（草稿租約也會產生）。房客：電話（共用聯絡人精靈的 `_partner_ids_by_phone_digits`）→ 名稱，多筆就略過並警告。
  - **抄表度數**：依（物件、房號、月份）排序後寫入，`READING_TO_BILL_MONTH_OFFSET = 1`（定義在 `rent_schedule.py`，與 `meter_month`、快速抄表共用；找收租明細用 `ggandy.rent.schedule._find_unit_month_schedule(unit, month)`）：M 月讀數 → M+1 月收租明細的 `*_curr_reading`，靠收租明細 `write` 的 `_propagate_meter_reading` 往後帶上期度數。非依度數計費、已過帳、讀數倒退 → 警告略過。
  - **帳單調整**：找該月收租明細，寫 `rent_amount`、`water_fixed_fee`（僅固定水費）、`note`；已有帳單就不改。`電費（核對用）` 只和 `electricity_amount` 比對。
  - 最後 `_check_suspicious_electricity`：收租明細電費 > 月租就警告（抓入住度數填 0 這種錯）。
  - **預覽**：在 `cr.savepoint()` 裡跑完整流程 → `flush_all()` → 丟 `_PreviewRollback` 還原 → `invalidate_all()`；預覽建立的租約用 `（預覽）…` 名稱，不取正式編號。每列寫入也包 savepoint，單列失敗只記警告。
- 測試資料重置：`notebook/scripts/reset_property_tenants.py`（預設 `PROP/2026/0134`、只刪 `demo-` 開頭房客，`COMMIT=1` 才寫入）。

### 包租獲利試算（TransientModel）

- `ggandy.quick.meter.reading.wizard`（`models/quick_meter_reading_wizard.py`）：物件 form `<header>` 的「快速抄表」（`ggandy.property.action_open_quick_meter_reading()`，target new）。主模組物件 form 的 `<header>` 只放這顆按鈕，整備模組用 `//header` position inside 接在後面（快速抄表 → 開始整備 → 整備採購 → 同步庫存位置）。
  - 兩步驟：`state` edit → `action_preview()`（先跑 `_analyze()`，格數不對直接 `UserError` 留在貼上步驟）→ preview（隱藏貼上欄位、月份唯讀，顯示 `preview_html`）→ `action_apply()`；`action_back()` 回 edit。兩個按鈕都回傳 `_reopen_wizard()`。`write_count` 為 0 時不顯示「確認寫入」。`bill_month_hint` 顯示在月份旁（→ 寫進 N 月帳單）。
  - `meter_month`：動態 Selection（`_get_meter_month_selection`，本月往回 12 個月），預設本月；帳單月份 = 電表月份 + `READING_TO_BILL_MONTH_OFFSET`。
  - 不貼房號：`room_order_html` 顯示 `property_id.unit_ids`（依 `sequence`）的編號順序，度數列依序對應。`_parse_rows()` 回傳 `[(電表月份, [(房間, 度數文字)])]`：
    - 一列：用 `meter_month`；去掉開頭非數字的那格（連 A 欄「9月」複製）。
    - 多列：每列第一格必須是月份（`_parse_month_label`：`9月` / `2026/9` / `2026-09`；只有月份時比本月大就算去年），月份重複、未來月份、認不出來 → `UserError`（帶列號）；依月份排序。
    - 共用 `_match_cells`：去掉超出房間數的尾端空白格；格數 ≠ 房間數 → `UserError`。
  - `_analyze()` 回傳 `[{meter_month, bill_month, lines}]`，由舊到新；`pasted[unit.id] = (帳單月份, 度數)` 記錄這次會生效的值，下個月的「上期度數」優先用它（連續月份時），其他用期次上的 prev。預覽 `_render_preview` 多月時每段加標題；`action_apply` 由舊到新寫入，靠 `_propagate_meter_reading` 往後帶。
  - `_analyze()`（`preview_html` / `write_count` 與 `action_apply()` 共用）：每格狀態 write / unchanged / skip（空白或 0、非依度數計費）/ error（度數非數字、沒有收租明細、已過帳、小於上期）。
  - `action_apply()`：每間 `cr.savepoint()` 寫 `electricity_curr_reading`（走收租明細 `write()` 的帶上期度數與草稿帳單同步），回傳 `display_notification` + `next: act_window_close`（物件 form 會重新載入）；一間都沒寫且沒有「未變更」時 `UserError`。只處理電錶。
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

### 版本資訊頁（client action）

選單「整合工具 → 版本資訊」開 `ir.actions.client`（`action_ggandy_release_notes`，tag `ggandy_release_notes`），對應 OWL component `GgandyReleaseNotes`（`static/src/release_notes/release_notes.js`）。純前端靜態頁：沒有 model、不寫資料庫，內容全在 OWL 範本 `static/src/release_notes/release_notes.xml`，樣式是 `ggandy_backend.scss` 的 `.o_ggandy_release_notes`。

- **每週更新一次**：把這一週使用者看得到的變化併成一版，加在時間軸 `<ol>` 最上面，把「最新」徽章移過去，並把 `__manifest__.py` 版本號 +1（v1.9 對應 `19.0.1.9.0`）。
- 每條重點用 `o_ggandy_release_tag_new`／`_improve`／`_fix`（新功能／改善／修正），用白話寫使用者能做什麼。
- 每版最後的「Lucas 碎碎念」（`o_ggandy_release_mood`）只放 Lucas 自己提供的文字，不代寫。

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

> **現況（2026-10-10 與老闆討論）**：庫存與採購現階段不是主力，目前這套架構（整備專案、採購帶分析帳戶、`WH/庫存/{物件}/{房間}` 庫存位置）先維持、夠用，之後視情況再往下開發。庫存位置只在背景記錄：開始整備時照常建立，物件管理資訊仍顯示、「同步庫存位置」按鈕保留，但物件 form 的房間分頁已不顯示庫存位置欄位。

depends：`ggandy_property_management`、`sale_project`、`project_purchase`、`project_account`、`project_purchase_stock`（帶入 `stock`；主模組不依賴庫存）。不新增模型，只 inherit `ggandy.property`、`ggandy.property.unit`、`res.config.settings`。

| 欄位 / 方法 | 說明 |
| --- | --- |
| `preparation_project_id` | 整備專案，`copy=False`、readonly；`models.Constraint unique(preparation_project_id)` |
| `preparation_progress` | Char，`已完成/全部 (百分比%)`，完成數 = 專案 `task_count - open_task_count`（與 Odoo 專案看板的進度同算法，取消也算完成；compute_sudo），給智慧按鈕（自訂 `o_stat_info`，不用 statinfo widget） |
| `action_start_preparation()` | 冪等：已有專案就直接開啟。檢查 manager 群組、`management_mode == master_lease`、`owner_id` → 以 sudo 從範本建立專案 → 補建分析帳戶 → 回寫物件 |
| `stock_location_id`（物件、房間） | 對應的 `stock.location`，`copy=False`、readonly、`ondelete=set null` |
| `_ensure_preparation_sale_order(project)` | 找 `project_id`＋`partner_id = owner_id`＋未取消的銷售單；沒有就 sudo 建單（帶一行 0 元 `product_product_master_lease_service_fee`「包租服務費」）並 `action_confirm()`；沿用的單沒有非 `display_type` 明細也補這一行。原因：`sale_project` 的 `action_view_sos()` 是用 `_fetch_sale_order_items()` 從明細反推銷售單，空單在專案的「銷售訂單」分頁看不到 |
| `_ensure_stock_locations()` | 冪等。公司第一個倉庫的 `lot_stock_id` → `{物件名稱}` → 每個 active 房間 `{房間名稱}`；已連結就跳過，否則同上層同名的 internal 位置沿用，再沒有才建立；回傳（新建數, 沿用數） |
| `action_sync_stock_locations()` | 包租且已有整備專案；manager 才可按；呼叫 `_ensure_stock_locations()` 並跳通知 |
| `action_view_preparation_project()` | 回傳專案的 `action_view_tasks()` |
| `action_create_preparation_purchase()` | 包租且已有整備專案才可用；開新的 `purchase.order` form，context `default_project_id` = 整備專案（與 Odoo `project_purchase` 的 `action_open_project_purchase_orders` 同做法） |
| `purchase.order.ggandy_is_preparation`（`models/purchase_order.py`） | 非儲存 compute：`project_id` 是某物件的 `preparation_project_id`（sudo 查，採購人員不一定能讀物件）。`views/purchase_order_views.xml` 讓 `partner_id` domain = `[('is_ggandy_vendor', '=', True)] if ggandy_is_preparation else []`，context 帶 `default_is_ggandy_vendor` |

```text
物件「開始整備」
  → env.ref("ggandy_property_project.project_preparation_template")
       is_template → action_create_from_template({name: 整備-{物件}, partner_id: 房東, allow_billable, company_id, user_id: 管理人員, date_start})
       範本不存在 → project.project.create(只帶 7 個預設階段)
  → project.account_id 為空 → project._create_analytic_account()   # account_id copy=False，範本複製不會帶
  → property.preparation_project_id = project
  → _ensure_preparation_sale_order(project)    # 帶 0 元包租服務費，已確認（重新開立發票成本需要 state = sale）
  → _ensure_stock_locations()                  # WH/庫存/{物件}/{房間}

sale.order / purchase.order 的 project_id = 整備專案
  → 明細 _compute_analytic_distribution 帶入 project._get_analytic_distribution()
  → 帳單明細帶分析 → 專案獲利能力 / 分析報表
```

- `data/project_preparation_data.xml` 為 `noupdate="1"`：範本專案先建，任務階段再用 `project_ids` 掛到範本上（沒有專案的階段會被 `project.task.type._default_user_id` 當成個人階段），最後是 12 個範本任務。
- 使用者在 UI 改範本任務，升級模組不會覆蓋。
- 房間改名不會改位置名稱；封存的房間不建位置；刪除房間不刪位置（位置可能有庫存）。
- 廠商帳單「重新開立發票成本」轉到銷售單的明細（`is_expense`）若是商品類，`sale_stock` 會照常產生交貨單；目前由使用者手動取消（從交貨單轉單的流程 Odoo 有 `skip_procurement`，帳單流程沒有）。

## 5. 排程總表

| Cron 名稱 | Model.method | 頻率 |
| --- | --- | --- |
| GGAndy：自動建立租金帳單 | `ggandy.rent.schedule._cron_create_due_invoices` | 1 天 |
| GGAndy：建立逾期租金提醒 | `ggandy.rent.schedule._cron_create_overdue_rent_activities` | 1 天 |
| GGAndy：房客租約到期自動結案 | `ggandy.lease._cron_expire_ended_leases` | 1 天 |
| GGAndy：自動建立房東 Vendor Bill | `ggandy.owner.contract._cron_create_owner_vendor_bills` | 1 天 |
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
