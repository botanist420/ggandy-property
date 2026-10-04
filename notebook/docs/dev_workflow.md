# GGAndy 開發流程手冊

## 1. 本機 Docker 環境（Compose project `ggandy_erp19`）

| 項目 | 值 |
| --- | --- |
| Odoo service / container | `ggandy_web` / `ggandy_odoo19_web`（image `odoo:19.0`，Python 3.12） |
| PostgreSQL service / container | `ggandy_db` / `ggandy_postgres17_db`（`pgvector/pgvector:pg17-trixie`） |
| Odoo Web | <http://localhost:1025>（容器 8069） |
| WebSocket / gevent | `localhost:1026`（容器 8072） |
| PostgreSQL | `localhost:5555`（容器 5432） |
| 資料庫 | `ggandy_dev` |
| Volumes | `ggandy_odoo19_data`（filestore、session）、`ggandy_postgres17_data` |
| Network | `ggandy_erp19_network` |

容器內掛載：

| 主機 | 容器 |
| --- | --- |
| `docker/ggandy/addons` | `/mnt/extra-addons` |
| `docker/ggandy/config/odoo.conf` | `/etc/odoo/odoo.conf`（ro） |
| `docker/ggandy/config/requirements.txt` | `/etc/odoo/requirements.txt`（ro） |
| `docker/ggandy/entrypoint.sh` | `/mnt/ggandy-entrypoint.sh`（先 pip install requirements，再交給官方 entrypoint） |

環境變數來自 `docker/ggandy/config/ggandy.env`（`HOST`、`PORT`、`POSTGRES_USER`、`POSTGRES_PASSWORD`、`POSTGRES_DB`）。

## 2. 修改程式後要做什麼

| 改了什麼 | 動作 |
| --- | --- |
| method 內的 Python 邏輯 | `docker restart ggandy_odoo19_web` |
| 新增 / 修改 field、model | 升級模組 |
| XML view、menu、action、security、data | 升級模組 |
| `data/*` 裡 `noupdate="1"` 的紀錄（cron、sequence、product） | 升級**不會**覆蓋既有紀錄；要改既有值需手動或寫 migration |
| `requirements.txt` | `docker restart ggandy_odoo19_web`（entrypoint 會重新 pip install） |

升級模組：

```bash
docker exec ggandy_odoo19_web sh -c 'odoo -c /etc/odoo/odoo.conf -d ggandy_dev \
  --db_host="$HOST" --db_port="$PORT" --db_user="$POSTGRES_USER" --db_password="$POSTGRES_PASSWORD" \
  -u ggandy_property_management,ggandy_property_telegram --stop-after-init --no-http'
docker restart ggandy_odoo19_web
```

## 3. Odoo shell

```bash
# 互動模式
docker exec -it ggandy_odoo19_web sh -c 'odoo shell -c /etc/odoo/odoo.conf -d ggandy_dev \
  --db_host="$HOST" --db_port="$PORT" --db_user="$POSTGRES_USER" --db_password="$POSTGRES_PASSWORD" --no-http'

# 執行 script（預設不 commit）
docker exec -i ggandy_odoo19_web sh -c 'odoo shell -c /etc/odoo/odoo.conf -d ggandy_dev \
  --db_host="$HOST" --db_port="$PORT" --db_user="$POSTGRES_USER" --db_password="$POSTGRES_PASSWORD" \
  --no-http --log-level=warn' < notebook/scripts/check_user_online.py
```

常用片段：

```python
# 手動跑 cron 邏輯
env["ggandy.rent.schedule"]._cron_create_due_invoices()
env["ggandy.owner.contract"]._cron_create_owner_vendor_bills()
env["ggandy.accounting.overview"].ensure_period_records()

# 看某單位本月帳務
ov = env["ggandy.accounting.overview"].search([("unit_id.name", "=", "301")], limit=1)
print(ov.rent_receivable, ov.rent_collected, ov.owner_payable, ov.net_cash_flow)

# 要保存異動
env.cr.commit()
```

## 4. SQL

```bash
# psql（容器內）
docker exec -it ggandy_postgres17_db sh -c 'psql -U "$POSTGRES_USER" -d ggandy_dev'

# 主機上用 pgAdmin / DBeaver：localhost:5555，帳密見 ggandy.env
```

常用查詢集：[`scripts/ggandy_common.sql`](../../scripts/ggandy_common.sql)（24 段，涵蓋聯絡人、物件、單位、租約、期次、逾期、帳單、報修、成本、總表、狀態一致性檢查）。

注意：帳務總表金額、`collection_state`、照片欄位都是**非 stored compute**，資料表裡沒有這些欄位，只能用 ORM 查。

## 5. 除錯

```bash
docker logs -f --tail 200 ggandy_odoo19_web
docker exec ggandy_postgres17_db sh -c 'psql -U "$POSTGRES_USER" -d ggandy_dev -c \
  "select cron_name, active, lastcall, nextcall from ir_cron where cron_name::text ilike '"'"'%GGAndy%'"'"'"'
```

- 想看 Odoo 原生 view 結構再決定 xpath：`docker exec ggandy_odoo19_web grep -n ... /usr/lib/python3/dist-packages/odoo/addons/<module>/views/*.xml`
- Telegram 收不到訊息：確認「本機輪詢模式」已勾、token 正確、Bot 沒有設 webhook、使用者有 `telegram_enabled` 且屬於管理員群組；看「包租代管 → Telegram → 訊息紀錄」。

## 6. 練習 / 研究腳本（notebook/scripts）

不使用 Jupyter，也**不需要**把 `notebook/` 掛載進容器：腳本留在主機，用 stdin 餵給容器內的 `odoo shell` 執行（見 §3）。

| 腳本 | 用途 |
| --- | --- |
| `orm_playground.py` | ORM 入門：物件 → 單位 → 租約 → 期次的資料關係（純查詢，結尾 rollback） |
| `check_user_online.py` | 查某使用者登入 / 在線狀態 / 裝置；用 `TARGET_LOGIN` 指定帳號（需 `docker exec -e TARGET_LOGIN=...`） |
| `test.py` | 個人 scratch（gitignore） |

VS Code：根目錄 `.vscode/tasks.json` 有「Odoo shell: 執行目前開啟的檔案」task（Terminal → Run Task），會把目前編輯中的 `.py` 餵進容器執行。

寫新腳本時：開頭沿用 `if TYPE_CHECKING: env = cast(Environment, None)` 讓 Pylance 不報錯；預設只查詢，需要寫入才明確 `env.cr.commit()`。

## 7. Git

- 不要 commit `docker/ggandy/config/odoo.conf` 的真實 `admin_passwd`；`config/ggandy.env` 已 gitignore。
- Commit 訊息風格：`feature: ...`、`update: ...`、`fix: ...`、`add: ...`。
- Remote：`git@github.com:botanist420/ggandy-property.git`，主分支 `master`。
