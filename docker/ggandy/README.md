# GGAndy Odoo 19 開發環境

此環境與其他 Compose 專案使用不同的 project、service、container、network、volume 與主機連接埠名稱。

## 名稱與連接埠

- Compose project：`ggandy_erp19`
- Odoo service / container：`ggandy_web` / `ggandy_odoo19_web`
- PostgreSQL service / container：`ggandy_db` / `ggandy_postgres17_db`
- Odoo Web：<http://localhost:1025>
- WebSocket / gevent：主機 `1026` 對應容器 `8072`
- Odoo data volume：`ggandy_odoo19_data`
- PostgreSQL data volume：`ggandy_postgres17_data`
- Network：`ggandy_erp19_network`

PostgreSQL 沒有發布到主機連接埠，只能由此 Compose network 中的服務存取。

## 啟動前設定

請先修改：

- `config/ggandy.env` 的 `POSTGRES_PASSWORD`
- `config/odoo.conf` 的 `admin_passwd`（Odoo 資料庫管理密碼，不是使用者登入密碼）

## 指令

請在專案根目錄執行：

```bash
docker compose -f docker/ggandy/docker-compose.yml config
docker compose -f docker/ggandy/docker-compose.yml up -d
docker compose -f docker/ggandy/docker-compose.yml down
```

一般的 `down` 不會刪除 named volumes。不要執行 `down -v`，除非確定要刪除 GGAndy 的 Odoo 與 PostgreSQL 資料。
