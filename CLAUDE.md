# CLAUDE.md

這份檔案是給 Claude Code 的專案指引。商業流程說明請看 [README.md](README.md)，模型與計算細節請看 [notebook/docs/architecture.md](notebook/docs/architecture.md)，代辦清單在 [notebook/TODO.md](notebook/TODO.md)。

## 專案概要

- GGAndy 包租代管公司的 Odoo **19.0** Community 客製化模組（Docker 開發環境）。
- 使用者是台灣團隊：UI 字串、欄位 `string`、`help`、錯誤訊息、註解、commit 訊息都用**繁體中文**；程式識別字（model、field、method）用英文。
- 兩個模組，都放在 `docker/ggandy/addons/`（掛載到容器 `/mnt/extra-addons`）：
  - `ggandy_property_management`：主模組，depends `base, mail, contacts, account`。
  - `ggandy_property_telegram`：Telegram 管理員 Bot，depends 主模組。
- 會計本地化 `l10n_tw` 已安裝在 `ggandy_dev`；開帳單時需要 `sale` / `purchase` 日記帳。

## 常用指令

全部在 repo 根目錄執行。容器：`ggandy_odoo19_web`（Odoo）、`ggandy_postgres17_db`（PostgreSQL）。資料庫：`ggandy_dev`。

```bash
# 啟動 / 停止（不要用 down -v，會刪資料）
docker compose -f docker/ggandy/docker-compose.yml up -d
docker compose -f docker/ggandy/docker-compose.yml down

# 升級模組（改了 field、XML、security、data 後必做）
docker exec ggandy_odoo19_web sh -c 'odoo -c /etc/odoo/odoo.conf -d ggandy_dev \
  --db_host="$HOST" --db_port="$PORT" --db_user="$POSTGRES_USER" --db_password="$POSTGRES_PASSWORD" \
  -u ggandy_property_management --stop-after-init --no-http'
docker restart ggandy_odoo19_web
# 只改 method 內的 Python 邏輯 → 只要 docker restart ggandy_odoo19_web

# Odoo shell（stdin 餵 script；預設不 commit，要保存需 env.cr.commit()）
echo 'print(env["ggandy.property"].search_count([]))' | docker exec -i ggandy_odoo19_web sh -c \
  'odoo shell -c /etc/odoo/odoo.conf -d ggandy_dev --db_host="$HOST" --db_port="$PORT" \
   --db_user="$POSTGRES_USER" --db_password="$POSTGRES_PASSWORD" --no-http --log-level=warn'

# SQL
docker exec ggandy_postgres17_db sh -c 'psql -U "$POSTGRES_USER" -d ggandy_dev -c "select count(*) from ggandy_property"'

# Log
docker logs -f --tail 200 ggandy_odoo19_web

# Odoo 19 原始碼（查 base view / 方法簽名時用，不要憑記憶猜）
docker exec ggandy_odoo19_web grep -n '<pattern>' /usr/lib/python3/dist-packages/odoo/addons/account/views/account_move_views.xml
```

目前**沒有自動化測試**（沒有 `tests/`）。驗證方式：升級模組無錯 → 用 Odoo shell 或 SQL 確認資料 → 必要時在 <http://localhost:1025> 手動操作。若新增測試，放在 `<module>/tests/`，用 `odoo ... -u <module> --test-enable --test-tags /<module> --stop-after-init --no-http`。

## 架構重點

### 模型地圖（`ggandy_property_management`）

| 模型 | 檔案 | 重點 |
| --- | --- | --- |
| `ggandy.property` | `models/property.py` | 物件；編號 `PROP/%(year)s/`；`owner_payment_day` 會預設帶入房東合約 |
| `ggandy.property.unit` | `models/property_unit.py` | 出租單位；`display_name` = `物件 / 單位`；格局／維修照片是 compute 的 `ir.attachment` Many2many |
| `ggandy.owner.contract` | `models/owner_contract.py` | 房東合約（包租 / 代管）；cron 建房東 Vendor Bill |
| `ggandy.lease` | `models/lease.py` | 房客租約；生效時拆月建期次 |
| `ggandy.rent.schedule` | `models/rent_schedule.py` | 租金期次；建客戶 Invoice；逾期活動 |
| `ggandy.maintenance.request` | `models/maintenance_request.py` | 報修單 |
| `ggandy.accounting.overview` | `models/accounting_overview.py` | 帳務總表，**每單位 × 每月一列**，金額全是非 stored compute |
| `ggandy.google.sheet.*.import.wizard` | `models/google_sheet_import_wizard.py` | TransientModel；物件匯入（pandas 讀 CSV export）；聯絡人匯入尚未實作 |
| `res.partner`（inherit） | `models/res_partner.py` | `is_ggandy_owner / tenant / vendor` 身分旗標 |
| `account.move`（inherit） | `models/account_move.py` | `ggandy_lease_id`、`ggandy_owner_contract_id`、`ggandy_expense_*`、`ggandy_settlement_period_*` |
| `ir.attachment`（inherit） | `models/ir_attachment.py` | `ggandy_request_date` 給維修照片排序 |
| `report.ggandy_property_management.report_lease_contract` | `report/lease_contract_report.py` | 房客租約 PDF 的資料準備（出租人判斷、民國日期）；範本與 action 在同目錄 `.xml` |

Telegram 模組：`ggandy.telegram.log`（`models/telegram_log.py`，指令處理與 cron 都在這）、`res.users` 綁定欄位、`res.config.settings`（token 存 `ir.config_parameter`，key 前綴 `ggandy_property_telegram.`）、`services/telegram_service.py`（純 `requests` 的 Bot API client）。

### 自動化（全在 `data/*_cron.xml`，`noupdate="1"`）

| Cron | 方法 | 頻率 |
| --- | --- | --- |
| 自動建立租金帳單 | `ggandy.rent.schedule._cron_create_due_invoices` | 每日 |
| 建立逾期租金提醒 | `ggandy.rent.schedule._cron_create_overdue_rent_activities` | 每日 |
| 自動建立房東 Vendor Bill | `ggandy.owner.contract._cron_create_owner_vendor_bills` | 每日 |
| 更新帳務總表月份 | `ggandy.accounting.overview._cron_ensure_current_period_records` | 每日 |
| Telegram 接收指令 | `ggandy.telegram.log._cron_poll_updates` | 每分鐘 |
| Telegram 逾期摘要 | `ggandy.telegram.log._cron_send_overdue_rent_digest` | 每日 |

Cron 方法的慣例：逐筆 `try/except Exception` + `_logger.exception(...)`，單筆失敗不中斷整批。所有建帳單的方法都是**冪等**的（先查既有帳單再建立），新增類似功能時保持這個特性。

### 改程式時要知道的連動

- **身分旗標是在 `create`/`write` 裡自動寫的**：物件/房東合約 → `is_ggandy_owner`；租約 → `is_ggandy_tenant`；報修單廠商 → `is_ggandy_vendor`。`ggandy.property.owner_id` 的 domain 是 `is_ggandy_owner = True`。
- **租約生效** (`action_activate`)：單位 → `occupied`、`_generate_rent_schedule()`、然後呼叫**全域**的 `_cron_create_due_invoices()`。
- **單位狀態**只會在 `vacant`↔`occupied` 之間自動切換（`_refresh_from_active_leases`），`reserved / maintenance / inactive` 尊重手動設定。
- **房東合約生效**會回寫物件的 `management_mode`、`owner_id`、`acquisition_date`、`management_end_date`。
- **重疊檢查**：同物件同期間只能一份 active 房東合約；同單位同期間只能一份 active 租約（`@api.constrains`）。
- **帳單用的產品**以 xmlid 取得：`product_product_rent`、`product_product_management_fee`、`product_product_owner_settlement`（`data/product_data.xml`）。
- **帳務總表** `_unique_unit_period` 用 Odoo 19 的 `models.Constraint`；`init()` 會 drop 舊的 property-period constraint。flag 欄位（`has_*`）的 search 是 `search([]).filtered(...)`，資料量大時會慢。
- **Telegram 授權**：`telegram_user_id` + `telegram_enabled` + 使用者需屬於 `group_property_manager`；只處理私訊；查詢用 `with_user(user).with_company(company)` 套用記錄規則。

### 權限

- 群組：`group_property_user`（無刪除）、`group_property_manager`（全權限，含 admin/root）。定義在 `security/property_management_security.xml`，使用 Odoo 19 的 `res.groups.privilege`。
- 新增模型時：在 `security/ir.model.access.csv` 加 user + manager 兩列；有 `company_id` 就在 security XML 的 `noupdate` 區塊加多公司 `ir.rule`；TransientModel 也需要 access 列。

## 程式慣例

- 每個欄位都寫中文 `string`，業務相關欄位寫清楚的中文 `help`（使用者游標停在欄位上會看到，語氣親切、會說明影響範圍）。
- `_inherit = ["mail.thread", "mail.activity.mixin"]` + 重要欄位 `tracking=True`。
- 有 `company_id`（預設 `self.env.company`）與 `currency_id`（related `company_id.currency_id`, stored）；金額用 `fields.Monetary`。
- 編號：`name`/`code` 預設 `"New"`，在 `@api.model_create_multi create()` 用 `ir.sequence.next_by_code()` 取號；sequence 在 `data/sequence_data.xml`。
- 狀態按鈕方法叫 `action_*`，用 `self.filtered(lambda r: r.state == ...)` 只處理合法狀態；不合法時 `raise UserError("中文訊息")`。
- Smart button 方法 `action_view_*` 回傳 `ir.actions.act_window` dict，並帶 `default_*` context。
- 金額 / 日期驗證用 `@api.constrains` + `ValidationError`。
- 「繳租日 / 結算日」1–31，短月用 `calendar.monthrange` 取月底；月份區間用 `relativedelta(months=1, days=-1)`。
- Odoo 19 語法：view 用 `<list>`（不是 `<tree>`）、`invisible="..."` / `readonly="..."` 表達式（不是 `attrs`）、kanban 用 `<t t-name="card">`、settings 用 `<app>/<block>/<setting>`。
- 新增 view / data 檔案要加到 `__manifest__.py` 的 `data`（順序：security → data → views → menu；`accounting_overview_views.xml` 在 `menu_views.xml` 之後，因為它引用 `menu_ggandy_root`）。
- 新增 model 檔案要加到 `models/__init__.py`。
- 外部 Python 套件要加到 `docker/ggandy/config/requirements.txt`（容器啟動時由 `entrypoint.sh` pip install），並在程式內延遲 import + 友善錯誤訊息（參考 `_read_google_sheet_csv`）。

## 注意事項

- `docker/ggandy/config/odoo.conf` 被 git 追蹤，但本機版本含真的 `admin_passwd`；**不要 commit 這個檔案的密碼變更**。`config/ggandy.env` 已被 gitignore。
- Telegram Bot token 只存在資料庫 `ir.config_parameter`，不要寫進程式碼、文件或 log。
- 不要執行 `docker compose down -v`、不要 drop `ggandy_dev`。
- Commit 訊息格式沿用既有風格：`feature: ...`、`update: ...`、`fix: ...`、`add: ...`。
- `notebook/` 是學習 / 研究用：文件（`notebook/docs/`）、代辦（`notebook/TODO.md`）、Odoo shell 練習腳本（`notebook/scripts/`，`test.py` 被 gitignore）。**不使用 Jupyter**，`notebook/` 也刻意不掛載進容器：腳本一律用 `docker exec -i ... odoo shell ... < notebook/scripts/xxx.py` 從主機餵進去。
- `scripts/ggandy_common.sql` 是常用查詢集；新增欄位或模型後，若有常用查詢需求可順手補上。
- 完成功能後，若行為有變，同步更新 `README.md`、`notebook/docs/architecture.md`，並把完成的項目從 `notebook/TODO.md` 勾掉。
