# notebook/

學習、研究與開發文件的地方。正式程式碼在 `docker/ggandy/addons/`，這裡的東西不會被 Odoo 載入，也沒有掛載進容器（腳本用 stdin 餵給 `odoo shell`）。

| 路徑 | 內容 |
| --- | --- |
| [TODO.md](TODO.md) | 代辦清單：已知 bug、流程缺口、改善項目 |
| [docs/architecture.md](docs/architecture.md) | 資料模型關係、狀態流程、計算公式、排程、編號規則 |
| [docs/dev_workflow.md](docs/dev_workflow.md) | Docker 環境、升級模組、Odoo shell、SQL、除錯、練習腳本執行方式 |
| `scripts/orm_playground.py` | Odoo ORM 入門：從物件 → 單位 → 租約 → 期次走一遍資料關係（純查詢） |
| `scripts/check_user_online.py` | 用 Odoo shell 查某使用者的登入、在線狀態與裝置（`TARGET_LOGIN` 環境變數指定帳號） |
| `scripts/test.py` | 個人 ORM 練習 scratch（已 gitignore） |

## 新增文件的慣例

- 規格、設計、研究筆記放 `docs/`，檔名用英文小寫加底線，內容用繁體中文。
- 練習 / 研究腳本放 `scripts/`，寫成 Odoo shell 腳本（執行方式見 [docs/dev_workflow.md](docs/dev_workflow.md) §3、§6）。
- 代辦一律加進 `TODO.md`，不要散落在各文件。
