# GGAndy 包租代管系統

GGAndy 是給包租代管公司使用的 Odoo 19 客製化 ERP。它把「房東、房客、物件、出租單位、租約、收租、維修、帳務」放在同一個系統裡，並把每月固定要做的事（開租金帳單、房東結算、逾期提醒、帳務總表）交給排程自動完成，讓老闆可以快速掌握公司營運狀況。

- Odoo 版本：19.0（Community，官方 `odoo:19.0` Docker image）
- 資料庫：PostgreSQL 17（`pgvector/pgvector:pg17-trixie`）
- 會計本地化：`l10n_tw`（台灣）
- 自訂模組：
  - `ggandy_property_management`：包租代管主模組
  - `ggandy_property_telegram`：Telegram 管理員 Bot

---

## 系統在管什麼

系統圍繞四種角色，全部都是 Odoo 的 `res.partner`，只靠勾選身分區分（同一個人可以同時是房東與房客）：

| 角色 | 說明 | 識別欄位 |
| --- | --- | --- |
| 房東 | 提供物件，和公司簽包租或代管合約 | `is_ggandy_owner` |
| 房客 | 和公司簽租約，每月繳租金、管理費與押金 | `is_ggandy_tenant` |
| 維修廠商 | 處理房屋維修與採購 | `is_ggandy_vendor` |
| 公司內部人員 | 管理物件、收租、報修、結算（Odoo 使用者） | 權限群組 |

這些身分大多會自動勾選：建立物件／房東合約時勾房東，建立租約時勾房客，報修單選了廠商就勾維修廠商。

主要資料流程：

```text
房東 (res.partner)
  ↓ 房東合約 ggandy.owner.contract ──（每月結算日）──→ 房東 Vendor Bill
物件 ggandy.property
  ↓
出租單位 ggandy.property.unit ──（格局照片 / 維修照片）
  ↓
房客租約 ggandy.lease
  ↓（租約生效時自動拆月）
租金期次 ggandy.rent.schedule ──（計費開始日到）──→ 房客租金 Invoice
  ↓
報修單 ggandy.maintenance.request / 採購與員工代墊 Vendor Bill
  ↓
GGAndy 帳務總表 ggandy.accounting.overview（每個出租單位 × 每個月一列）
```

---

## 日常商業流程

### 1. 建立物件與出租單位

每個管理中的房子先建立成「物件」，系統自動給物件編號（`PROP/2026/0001`）。物件記錄：

- 主要房東、共同屋主、內部管理人員
- 物件類型（整棟、公寓、透天、套房、商用…）
- 經營模式：包租、代管或混合
- 地址、開始管理日、管理截止日
- **匯款日期**：每月匯款給房東的日期，新增房東合約時會自動帶入成合約的結算日

物件底下可以有多個出租單位（套房、雅房、整層、店面、辦公室、車位）。出租單位記錄房號、樓層、坪數、房間／衛浴數、參考月租、押金月數與出租狀態（空房／已保留／已出租／維修中／停用）。

每個出租單位也記錄**水電費怎麼收**，電費、水費各自設定：

- 計費方式：依度數計費／每月固定金額／含在租金內／房客自繳（台電、水公司）。
- 依度數計費時填「每度電價／每度水價」，每間房可以不同（例如 5、5.5、4 元）。算法是 **（本月度數 − 上月度數）× 每度單價**。
- 每月固定金額時填固定電費／水費；另可記錄電錶、水錶編號或位置，抄表時對照用。
- 單價改動會留在 chatter，方便日後對帳。目前只記錄設定，還不會抄表或開水電帳單。

出租單位頁面另外有兩個照片分頁：

- **格局照片**：在該單位的 chatter 用迴紋針上傳圖片，就會自動出現在這裡。
- **維修照片**：自動彙整這個單位所有報修單（含已封存）chatter 上的圖片，依報修時間由近到遠排列。

> 物件也可以從 Google Sheet 批次匯入（見「整合工具」）。

### 2. 建立房東合約

房東合約代表「公司和房東之間怎麼合作」，編號 `OWNER/2026/00001`。

| 合約類型 | 每月應付房東 |
| --- | --- |
| 包租 | 每月保底租金（不論房客是否已繳） |
| 代管 | 當月實收租金 −代管費（百分比或固定金額；當月沒收到租金就不扣固定費） |

- 同一物件在同一期間只能有一份「生效中」的房東合約。
- 合約生效時，會把物件的房東、經營模式、管理期間同步更新。
- 每天排程會檢查：到了當月「房東付款／結算日」（短月自動用月底），就替合約建立當月房東 Vendor Bill，同一個月不會重複建立。

### 3. 建立房客租約

房客租約代表「公司和房客之間怎麼收租」，編號 `LEASE/2026/00001`。記錄物件、出租單位、主承租人與共同承租人、租期、每月租金、管理費、押金與每月繳租日。

- 選出租單位時會自動帶入參考月租與押金（參考月租 × 押金月數）。
- 同一出租單位在同一期間只能有一份生效租約。
- 租約生效後：出租單位變成「已出租」、依月份自動拆出租金期次（月中入住／退租的那一期，租金與管理費依當月實際天數按日計算，四捨五入到元），並立即替已到期的期次開立 Invoice。
- 已有過帳帳單的租約不能退回草稿或取消；終止或取消後，出租單位會依是否還有其他生效租約自動回到空房／已出租。
- **產生租約 PDF**：草稿或生效中的租約，按上方「產生租約 PDF」（或「列印」選單）就會下載一份「房屋租賃契約書」，內容依租約欄位帶入：出租人、承租人／共同承租人、物件地址、出租單位、租期（民國年）、租金、管理費、押金、繳租日，「合約備註」會印在特約事項。包租物件的出租人是公司；代管物件的出租人是房東，公司列為代管業者（丙方）。目前是簡單版範本，條款之後會依公司正式合約調整。
- **合約文件分頁**：把簽好名的合約、公證書、點交照片用租約 chatter 的迴紋針上傳，就會自動彙整在最後一個分頁（圖片顯示縮圖，PDF 等檔案顯示圖示，點了在新分頁開啟）。

### 4. 收租與帳單

每月租金先成為「租金期次」，再由期次建立客戶 Invoice（租金與管理費分兩行）：

- 每天排程：計費開始日已到、還沒開單的期次自動開 Invoice。
- 每天排程：已逾期未收齊的期次，替物件管理人員建立「逾期租金待處理」活動。
- 收租狀態：尚未開單、草稿帳單、待收款、部分收款、已收款、已逾期、已取消。

租金期次是營運管理用的角度，Invoice 是會計與收款用的角度。

### 5. 報修與維修費用

報修單（`REPAIR/2026/00001`）記錄物件與單位、報修房客、維修類型、優先度、內部負責人、維修廠商、預估／實際費用，以及費用由公司、房東、房客或待確認負擔。

- 選出租單位時自動帶出目前生效租約與房客。
- 流程：待處理 → 已指派 → 處理中 → 等待料件 → 已完成（完成前必須填寫處理結果）。
- 維修照片直接上傳到報修單 chatter，會自動彙整到出租單位的「維修照片」分頁。

---

## GGAndy 帳務總表

選單「包租代管 → GGandy帳務 → 營運總表」是給老闆看整體營運狀況的地方。

它以 **每個出租單位、每個月份** 為一列（每日排程會替所有啟用中的出租單位建立當月資料，也可以手動按「更新本月總表」），所有金額都是即時計算：

| 欄位 | 計算方式 |
| --- | --- |
| 租金應收 | 當月租金期次的租金 + 管理費 |
| 租金實收 | 已過帳 Invoice 的已收金額 |
| 租金未收 | 應收 − 實收 |
| 租金押金 | 當月開始的租約押金 |
| 房東應付／已付／待付 | 當月房東 Vendor Bill，依出租單位參考月租比例分攤到各單位（無參考月租則平均分攤） |
| 採購費用 | 歸屬到此物件與單位、類型為「採購費用」的 Vendor Bill |
| 員工代墊 | 歸屬到此物件與單位、類型為「公司員工代墊」的 Vendor Bill |
| 現金流小計 | 租金實收 + 押金 − 房東已付 − 採購費用 − 員工代墊 |

可以用「本月／上月／租金未收／房東待付／現金流為負／有成本／有押金」快速篩選，並依月份、物件、單位、房東分組，最下面有合計。

> 現金流小計反映的是現金進出，不等同會計損益。

---

## Telegram 行動指揮中心

`ggandy_property_telegram` 是給「包租代管 / 管理員」群組使用者的行動查詢工具。

| 指令 | 內容 |
| --- | --- |
| `/status` | 今日營運摘要：管理物件數、出租中／空房、本月應收／已收／未收、逾期租金、待處理維修、30 天內到期租約 |
| `/overdue` | 逾期租金清單（前 10 筆與未收合計） |
| `/leases` | 30 天內到期租約 |
| `/maintenance` | 待處理維修 |
| `/start`、`/help` | 指令說明 |

設定方式：

1. 包租代管 → Telegram → Telegram 設定（需系統管理員）：填入 BotFather 給的 Bot Token，按「測試 Telegram 連線」，並勾選「本機輪詢模式」。
2. 使用者私訊 Bot 傳 `/start`，Bot 會回覆該使用者的 Telegram User ID。
3. 到 Odoo 使用者頁面填入 Telegram User ID，並勾選「允許使用 GGAndy Telegram Bot」（使用者必須是包租代管管理員）。

Bot 透過 Odoo 排程每分鐘向 Telegram `getUpdates` 拉訊息，不需要公開 webhook，localhost 也能用。只接受私訊，所有收發紀錄都會留存。另外每天會把逾期租金摘要主動推播給已綁定的管理員。收發紀錄在「包租代管 → Telegram → 訊息紀錄」。

---

## 整合工具

選單「包租代管 → 整合工具」：

- **從 Google Sheet 匯入物件**：貼上 Google Sheet 連結（需可公開讀取），欄位為 `編號`、`案件名稱`（必填）、`經營模式`（包租／代管／混合）、`房東`、`地址`、`管理人員`、`匯款日期`（例如 `30號`）、`出租單位`（房號用半形逗號隔開，例如 `201,202,店面`）。用 `編號 + 案件名稱` 產生穩定的 external id，重複匯入會更新同一筆物件，不會重複建立；留空的欄位不會覆蓋既有資料。房東依名稱比對聯絡人，找不到時預設自動建立房東聯絡人；管理人員依名稱或登入帳號比對內部使用者，找不到就留空，並在匯入結果以警告列出「編號／案件名稱／管理人員」。出租單位只新增物件底下還沒有的房號，不會刪除。
- **從 Google Sheet 匯入聯絡人**：選單已建立，功能尚未實作。目前聯絡人匯入請使用 `scripts/google_res_partner.py`（XML-RPC 外部腳本）。
- **包租獲利試算**（僅管理者）：跟房東談包租前先試算划不划。輸入房間數、初始投入、免租裝潢期、包租期間、每間付房東、每間預估月租、出租率，即時算出每月與整段期間的賺賠、投資報酬率、年化報酬率（ROI）、幾個月回本，以及出租率、月租、付房東的不賠底線。純試算，不會寫入任何資料；尚未扣水電、維修、人事與稅。

---

## 權限

| 群組 | 權限 |
| --- | --- |
| 包租代管 / 使用者 | 可讀寫建立所有包租代管資料，不能刪除 |
| 包租代管 / 管理員 | 完整權限（含刪除），可使用 Telegram Bot、查看 Bot 紀錄 |

所有主要模型都有多公司記錄規則（`company_id in company_ids`）。

---

## 專案結構

```text
.
├── README.md                          ← 本文件
├── CLAUDE.md                          ← 給 Claude Code 的開發指引
├── docker/ggandy/                     ← Docker Compose 開發環境
│   ├── docker-compose.yml
│   ├── entrypoint.sh                  ← 啟動前安裝 config/requirements.txt
│   ├── config/
│   │   ├── odoo.conf
│   │   ├── ggandy.env.example         ← 複製成 ggandy.env（不進 git）
│   │   └── requirements.txt           ← 容器內額外 Python 套件（pandas、numpy）
│   └── addons/
│       ├── ggandy_property_management/
│       │   ├── models/                ← 物件、單位、合約、租約、期次、報修、帳務總表、匯入精靈
│       │   ├── views/
│       │   ├── report/                ← PDF 報表（房客租約）
│       │   ├── data/                  ← 編號規則、產品、排程
│       │   └── security/
│       └── ggandy_property_telegram/
│           ├── models/                ← Bot 紀錄與指令處理、使用者綁定、設定
│           └── services/              ← Telegram Bot API client
├── scripts/
│   ├── ggandy_common.sql              ← 常用 SQL 查詢（pgAdmin / psql）
│   └── google_res_partner.py          ← Google Sheet / CSV 匯入聯絡人（XML-RPC）
└── notebook/                          ← 學習用 notebook、開發文件與代辦
    ├── README.md
    ├── TODO.md
    └── docs/
```

---

## 開發與測試環境

### 服務與連接埠

| 項目 | 值 |
| --- | --- |
| Odoo Web | <http://localhost:1025> |
| Odoo WebSocket / gevent | `localhost:1026` |
| PostgreSQL | `localhost:5555`（容器內 `ggandy_db:5432`） |
| Odoo container | `ggandy_odoo19_web` |
| PostgreSQL container | `ggandy_postgres17_db` |
| 開發資料庫 | `ggandy_dev` |

### 第一次啟動

```bash
cp docker/ggandy/config/ggandy.env.example docker/ggandy/config/ggandy.env
# 修改 ggandy.env 的 POSTGRES_PASSWORD，以及 odoo.conf 的 admin_passwd
docker compose -f docker/ggandy/docker-compose.yml up -d
```

開啟 <http://localhost:1025> 建立資料庫後，到 Apps 安裝「GGAndy 包租代管」與「GGAndy Telegram 管理員 Bot」。開立帳單前請確認公司國家為台灣且已安裝會計本地化（需要銷售／採購日記帳）。

### 更新模組

修改 Python 或 XML 後：

```bash
docker exec ggandy_odoo19_web sh -c 'odoo -c /etc/odoo/odoo.conf -d ggandy_dev \
  --db_host="$HOST" --db_port="$PORT" --db_user="$POSTGRES_USER" --db_password="$POSTGRES_PASSWORD" \
  -u ggandy_property_management --stop-after-init --no-http'
docker restart ggandy_odoo19_web
```

更多開發指令（Odoo shell、SQL、除錯方式）請看 [notebook/docs/dev_workflow.md](notebook/docs/dev_workflow.md)；資料模型與計算邏輯請看 [notebook/docs/architecture.md](notebook/docs/architecture.md)。

---

## 老闆可以怎麼看這套系統

這套系統的核心不是只記資料，而是把營運節奏自動化：

- 合約生效後，系統知道什麼時候該收房客租金。
- 租金到期時，系統自動建立房客帳單；逾期了自動提醒負責人。
- 房東結算日到時，系統自動建立房東 Vendor Bill。
- 維修照片、維修與採購費用都能歸到物件與出租單位。
- 最後所有數字集中到 GGAndy 帳務總表；人在外面就用 Telegram 查。

老闆不用每次從房東合約、房客租約、發票、維修單裡一筆一筆對，可以先看總表掌握大方向，再往下追細節。
