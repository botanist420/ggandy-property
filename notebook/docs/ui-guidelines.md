# 介面排版規範

> GGAndy 包租代管模組的畫面寫法，目標只有一個：**版面乾淨、一眼看懂**。
> 新增或修改 form、分頁、wizard 時照這份做；樣式定義在
> [`ggandy_backend.scss`](../../docker/ggandy/addons/ggandy_property_management/static/src/scss/ggandy_backend.scss)。

---

## 1. 五條原則

| # | 原則 | 做法 |
| :-: | --- | --- |
| 1 | **先給資料，再給說明** | 說明放在分頁最上方，最多兩行，不要寫成一整段 |
| 2 | **一句話只講一件事** | 第一行講「這裡是什麼」，第二行講「要注意什麼」 |
| 3 | **重點只標一個** | 每行最多一個 `<strong>`，標的是關鍵名詞，不是整句 |
| 4 | **細節放 tooltip** | 欄位用法、影響範圍寫在欄位 `help`，畫面上不重複 |
| 5 | **空間給有內容的欄** | 數字欄固定寬度，最後放一個不固定寬度的文字欄吃掉剩餘空間 |

---

## 2. 分頁說明列 `o_ggandy_hint`

分頁頂端的說明一律用這個元件，**不要**用 `alert alert-info` 或裸的 `text-muted` 段落。

```xml
<div class="o_ggandy_hint">
    <i class="fa fa-info-circle" role="img" title="說明"/>
    <div>
        <div>本月帳單收上個月電費，所以<strong>電表月份是上個月</strong>；抄表度數請填該月月底讀數。</div>
        <div>填好會帶到下一期並更新草稿帳單，已過帳不能改。過去月份請到出租單位查看。</div>
    </div>
</div>
```

**外觀**：左側主色細線、淡灰底、小字灰色、`info-circle` 圖示；`<strong>` 會轉成正文色當作重點。

**規則**

- 最多 **2 行**，每行大約 **40 字以內**；寫不下就把細節搬去欄位 `help`。
- 第一行說明「這個分頁列的是什麼」，第二行說明「規則／限制／去哪裡改」。
- `<i class="fa …">` 一定要加 `title`，否則 Odoo 會出無障礙警告。
- 放在 inner `<group>` 裡時要加 `colspan="2"`，才會佔滿整欄。
- 只在特定狀態才需要的說明，直接在 `o_ggandy_hint` 加 `invisible="…"`。

**什麼時候不需要說明列**

- 純輸入的分頁（備註、問題說明、處理結果）：用欄位 `placeholder` 提示就好。
- 列表本身就看得懂的分頁（出租單位、租約紀錄、維修紀錄）。

---

## 3. 用字

| ✅ 這樣寫 | ❌ 不要這樣寫 |
| --- | --- |
| 用下方 chatter 的**迴紋針**上傳圖片，就會自動出現在這裡。 | 請用下方 chatter 的迴紋針上傳格局照片，圖片會自動顯示在這裡。 |
| 目前只記錄**代管費約定**。 | 第一版只記錄代管費約定；房東月結單與實際撥款會在確認金流規則後另行實作。 |
| 已過帳不能改。 | 帳單已過帳的月份不能再改。 |

- 畫面上的名稱用「」：「租金期次」分頁、「確認寫入」按鈕。
- 少用「請」開頭；直接講動作。
- 不寫開發用語（第一版、另行實作、model、record）。
- 數字、英文前後不加全形空白；中英混排的空格照一般習慣（`Telegram User ID`）。

---

## 4. 分頁內的列表

| 項目 | 規範 |
| --- | --- |
| 固定寬度 | 房號 `110px`、月份 `90px`、度數 `100px`、用量 `90px`、金額 `100px`（`width="…px"`） |
| 吸收剩餘空間 | 最後放一個**不設寬度**的文字欄（例如 `note` 備註、房客），否則 Odoo 會把多的空間平均分給所有欄位，畫面看起來很空 |
| 次要欄位 | `optional="hide"`（使用者可從右上角欄位選單勾回來） |
| 只用來判斷的欄位 | `column_invisible="True"` |
| 新增按鈕文字 | 用 `<control><create string="新增房間"/></control>` 改成業務用語 |
| 狀態 | `widget="badge"`，顏色見下方第 6 節 |
| 排序 | 房間類列表用 `default_order="unit_id"`（跟出租單位的拖曳順序一致）；歷史資料用日期由新到舊 |

---

## 5. 照片／文件卡片 `o_ggandy_photo_card`

格局照片、維修照片、合約文件都用同一種卡片：圖片固定 4:3、標題一行截斷、資訊用小字。

```xml
<t t-name="card" class="o_ggandy_photo_card">
    <a class="o_ggandy_photo_thumb" t-attf-href="/web/image/#{record.id.raw_value}?download=false" target="_blank">
        <img t-attf-src="/web/image/#{record.id.raw_value}/512x384" t-att-alt="record.name.value"/>
    </a>
    <field name="name" class="fw-bold text-truncate"/>
    <div class="o_ggandy_photo_meta"><field name="create_date"/> · <field name="create_uid"/></div>
</t>
```

- 資訊列不寫「上傳時間：」這類標籤，用 `·` 串起來就好。
- 非圖片檔（PDF 等）用 `<div class="o_image" t-att-data-mimetype="…"/>` 顯示檔案類型圖示，外層加 `d-flex align-items-center justify-content-center` 置中。

---

## 6. 顏色與狀態

| 用途 | class | 例子 |
| --- | --- | --- |
| 完成、成功、將寫入 | `success` | 已出租、已付款、將寫入 |
| 需要注意 | `warning` | 維修中、部分付款 |
| 進行中、資訊 | `info` | 已保留、處理中 |
| 錯誤、逾期、擋下 | `danger` | 逾期、無法寫入 |
| 沒有變化、略過 | `light` / 預設 | 空房、略過、已是此度數 |

按鈕：每個畫面**只有一顆** `btn-primary`（最主要的下一步）；其他用 `btn-secondary`。需要「提醒但不強迫」時，用兩顆同名按鈕依狀態切換 primary／secondary（例如出租單位的「新增房客租約」）。

---

## 7. Wizard

- 頂部用 `alert alert-info py-2 mb-3` + `<ol>` 寫**最多 3 個步驟**，每步一行。
- 有寫入動作的 wizard 一律**兩步驟**：先「預覽」→ 再「確認寫入」；預覽時把輸入欄收起來、只留結果。
- 沒有東西可做時不要顯示主要按鈕，改顯示一行 `alert-warning` 說明原因。
- 補充小提示放在輸入欄下方：`<div class="small text-muted">…</div>`。

---

## 8. 檢查清單

改完畫面後對一下：

- [ ] 說明列 ≤ 2 行，每行 ≤ 40 字，最多一個重點
- [ ] 沒有 `alert-info` 出現在一般 form 的分頁裡
- [ ] 數字欄有固定寬度，最後有一欄吃剩餘空間
- [ ] 每顆 `fa` 圖示都有 `title`
- [ ] 每個 `<page>` 都有 `name`（方便其他模組 xpath）
- [ ] 一個畫面只有一顆 `btn-primary`
- [ ] 升級模組無警告、重新整理後在淺色／深色模式都看過

---

## 附錄：目前套用的位置

| 畫面 | 分頁 | 說明列重點 |
| --- | --- | --- |
| 物件 | 當期帳單 | 只供檢視；改金額到租約「租金期次」 |
| 物件 | 電表維護 | 電表月份是上個月；過去月份到出租單位 |
| 出租單位 | 電表維護 | 電表月份的意思；過帳後不能改 |
| 出租單位 | 格局照片 | 用 chatter 迴紋針上傳 |
| 出租單位 | 維修照片 | 彙整所有報修單；到報修單上傳 |
| 房客租約 | 租金期次 | 每月一期、按日計算；抄表填「電錶本期」 |
| 房客租約 | 合約文件 | 用 chatter 迴紋針上傳 |
| 房東合約 | 包租／代管條件 | 保底租金的意思；代管目前只記約定 |
| 聯絡人 | 包租代管 | 可同時是房東、房客、廠商 |
| 使用者 | GGAndy Telegram | 操作 Bot 需要的三個條件 |
