# 讀 Google Sheet「資料快照」分頁（export_property_snapshot.py 產生的分段 CSV），在目前資料庫
# 重建一個物件：聯絡人 → 物件 → 出租單位 → 租約 → 租金期次 → 租約生效 → 草稿租金帳單。
# 物件編號、租約編號由目標資料庫自己取號；其餘資料逐欄對齊 Sheet，最後逐欄核對，有任何差異就不寫入。
# 說明文件：notebook/docs/property-snapshot.md
#
# 預設只試跑（最後 rollback，且不取正式編號），確認輸出「差異 0」後加 COMMIT=1 才真的寫入：
#   docker exec -i <odoo容器> sh -c 'odoo shell -c /etc/odoo/odoo.conf -d <資料庫> \
#     --db_host="$HOST" --db_port="$PORT" --db_user="$POSTGRES_USER" --db_password="$POSTGRES_PASSWORD" \
#     --no-http --log-level=error' < notebook/scripts/import_property_snapshot.py
#   docker exec -i -e COMMIT=1 <odoo容器> sh -c '...同上...' < notebook/scripts/import_property_snapshot.py
#
# 環境變數：
#   SHEET_URL        Sheet 的 CSV 匯出網址（預設：大安公館資料快照分頁）
#   COMMIT=1         核對無差異才 commit
#   ALLOW_DUPLICATE=1  目標資料庫已有同名物件時仍繼續（預設停下，避免重複插入）
#   CREATE_INVOICES=0  不建立草稿租金帳單（預設會替起租日到今天的期次建草稿帳單，和租約生效時一樣）
# 也可以在 stdin 前面先放一行 CSV_TEXT = "..." 直接餵 CSV 內容（本機測試用）。
import csv
import io
import os
import re
from datetime import date, datetime

import requests

from odoo.tools import float_compare, html2plaintext

SHEET_URL = os.environ.get(
    "SHEET_URL",
    "https://docs.google.com/spreadsheets/d/1sIjThJKjdEGCwa4yXWfwIhKilj3BfaUga_uLSS3HlzY/export?format=csv&gid=0",
)
COMMIT = os.environ.get("COMMIT") == "1"
ALLOW_DUPLICATE = os.environ.get("ALLOW_DUPLICATE") == "1"
CREATE_INVOICES = os.environ.get("CREATE_INVOICES", "1") == "1"
DRY_PREFIX = "（試跑）"

warnings, diffs = [], []


# ----------------------------------------------------------------------
# 讀 Sheet
# ----------------------------------------------------------------------
def load_csv_text():
    text = globals().get("CSV_TEXT")
    if text:
        return text
    response = requests.get(SHEET_URL, timeout=30)
    response.raise_for_status()
    if "text/csv" not in response.headers.get("Content-Type", ""):
        raise SystemExit(
            "Sheet 回傳的不是 CSV（多半是登入頁）：請把試算表共用設成「知道連結的任何人都能檢視」，"
            f"並確認 SHEET_URL 是 .../export?format=csv&gid=<分頁 gid>。目前網址：{SHEET_URL}"
        )
    response.encoding = "utf-8"
    return response.text


def parse_sections(text):
    """【段落名稱】下一列是欄位名稱，之後到空白列為止都是資料。"""
    sections, name, header = {}, None, None
    for row in csv.reader(io.StringIO(text)):
        cells = [cell.strip() for cell in row]
        first = cells[0] if cells else ""
        if first.startswith("【") and first.endswith("】"):
            name, header = first[1:-1], None
            sections[name] = []
        elif not any(cells):
            name = None
        elif name and header is None:
            header = cells
        elif name:
            sections[name].append(dict(zip(header, cells + [""] * (len(header) - len(cells)))))
    for required in ("聯絡人", "物件", "出租單位", "租約", "租金期次"):
        if required not in sections:
            raise SystemExit(f"Sheet 少了「【{required}】」段落")
    return sections


def num(value, default=0.0):
    value = (value or "").replace(",", "").strip()
    return float(value) if value else default


def opt_num(value):
    return num(value, None)


def to_date(value):
    value = (value or "").strip()
    if not value:
        return False
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    raise SystemExit(f"看不懂的日期：{value}")


def selection_key(model, field_name, label):
    if not label:
        return False
    selection = env[model]._fields[field_name].selection
    mapping = {text: key for key, text in selection}
    mapping.update({key: key for key, _text in selection})
    if label not in mapping:
        raise SystemExit(f"{model}.{field_name} 沒有選項「{label}」（可用：{'、'.join(text for _k, text in selection)}）")
    return mapping[label]


def plain(value):
    return " ".join(html2plaintext(str(value)).split()) if value else ""


def check(label, field, actual, expected, numeric=False):
    if numeric:
        same = float_compare(actual or 0.0, expected or 0.0, precision_digits=2) == 0
    else:
        same = (actual or "") == (expected or "")
    if not same:
        diffs.append(f"{label} {field}：系統 {actual!r} ≠ Sheet {expected!r}")


# ----------------------------------------------------------------------
# 建立資料
# ----------------------------------------------------------------------
def upsert_partners(rows):
    Partner = env["res.partner"]
    finder = env["ggandy.google.sheet.contact.import.wizard"]
    partners, created = {}, Partner
    for row in rows:
        name = row["名稱"]
        roles = row["身分"]
        flags = {"is_ggandy_owner": "房東" in roles, "is_ggandy_tenant": "房客" in roles}
        flags = {key: True for key, value in flags.items() if value}
        found = Partner.browse(finder._partner_ids_by_phone_digits(row["電話"])) if row["電話"] else Partner
        if not found:
            found = Partner.search([("name", "=", name)])
        if len(found) > 1:
            raise SystemExit(f"聯絡人「{name}」在目標資料庫有 {len(found)} 筆（電話或名稱相同），請先處理重複再匯入")
        if found:
            # 以 Sheet 為主：名稱、電話、Email 不一致就改成 Sheet 的值（Sheet 留空的不清掉）
            values, changes = dict(flags), []
            for column, field in (("名稱", "name"), ("電話", "phone"), ("Email", "email"), ("內部參考", "ref")):
                if row[column] and (found[field] or "") != row[column]:
                    values[field] = row[column]
                    changes.append(f"{column} {found[field] or '（空）'} → {row[column]}")
            found.write(values)
            detail = f"，已改成 Sheet 的值：{'；'.join(changes)}" if changes else ""
            warnings.append(f"聯絡人「{name}」：目標資料庫已存在（id {found.id}），沿用不新增{detail}")
            partners[name] = found
            continue
        values = {"name": name, **flags}
        for column, field in (("電話", "phone"), ("Email", "email"), ("內部參考", "ref")):
            if row[column]:
                values[field] = row[column]
        if row["備註"]:
            values["comment"] = row["備註"]
        partner = Partner.create(values)
        partners[name] = partner
        created |= partner
    return partners, created


def find_location(row):
    Country, State, City = env["res.country"], env["res.country.state"], env["res.city"]
    country = Country.search([("code", "=", row["國家"])], limit=1) if row["國家"] else Country
    if row["國家"] and not country:
        warnings.append(f"找不到國家「{row['國家']}」，物件國家留空")
    state = State
    if row["縣市"]:
        state = State.with_context(lang="zh_TW").search(
            [("country_id", "=", country.id), ("name", "=", row["縣市"])], limit=1
        )
        if not state:
            warnings.append(f"找不到縣市「{row['縣市']}」，物件縣市留空")
    city = City
    if row["行政區"]:
        domain = [("name", "=", row["行政區"])] + ([("state_id", "=", state.id)] if state else [])
        city = City.with_context(lang="zh_TW").search(domain, limit=1)
        if not city:
            warnings.append(f"找不到行政區「{row['行政區']}」，物件行政區留空")
    return country, state, city


def create_property(row, partners):
    country, state, city = find_location(row)
    manager = env["res.users"]
    if row["負責人"]:
        manager = env["res.users"].search(["|", ("name", "=", row["負責人"]), ("login", "=", row["負責人"])], limit=1)
        if not manager:
            warnings.append(f"找不到使用者「{row['負責人']}」，物件負責人留空，請之後手動指定")
    values = {
        "name": row["物件名稱"],
        "property_type": selection_key("ggandy.property", "property_type", row["物件類型"]),
        "management_mode": selection_key("ggandy.property", "management_mode", row["管理模式"]),
        "owner_id": partners[row["房東"]].id if row["房東"] else False,
        "manager_id": manager.id,
        "street": row["街道"] or False,
        "street2": row["街道2"] or False,
        "city": row["城市"] or False,
        "city_id": city.id,
        "state_id": state.id,
        "zip": row["郵遞區號"] or False,
        "country_id": country.id,
        "acquisition_date": to_date(row["取得日期"]),
        "management_end_date": to_date(row["管理到期日"]),
        "note": row["備註"] or False,
    }
    if row["房東付款日"]:
        values["owner_payment_day"] = int(num(row["房東付款日"]))
    if row["網路費"]:
        values["internet_fee"] = num(row["網路費"])
    if not COMMIT:
        values["code"] = DRY_PREFIX  # 試跑不取正式編號，避免還原後跳號
    return env["ggandy.property"].create(values), manager


def unit_values(row):
    return {
        "name": row["房號"],
        "sequence": int(num(row["排序"], 10)),
        "unit_type": selection_key("ggandy.property.unit", "unit_type", row["單位類型"]),
        "floor": row["樓層"] or False,
        "area": num(row["坪數"]),
        "bedroom_count": int(num(row["房數"])),
        "bathroom_count": int(num(row["衛浴數"])),
        "monthly_rent": num(row["月租"]),
        "deposit_months": num(row["押金月數"]),
        "electricity_billing_type": selection_key("ggandy.property.unit", "electricity_billing_type", row["電費方式"]),
        "electricity_rate": num(row["每度電價"]),
        "electricity_fixed_fee": num(row["固定電費"]),
        "water_billing_type": selection_key("ggandy.property.unit", "water_billing_type", row["水費方式"]),
        "water_rate": num(row["每度水價"]),
        "water_fixed_fee": num(row["固定水費"]),
    }


def schedule_values(row):
    values = {
        "period_end": to_date(row["期間迄"]),
        "due_date": to_date(row["繳款期限"]),
        "rent_amount": num(row["租金"]),
        "management_fee": num(row["管理費"]),
        "note": row["備註"] or False,
    }
    for kind, label in (("electricity", "電"), ("water", "水")):
        values[f"{kind}_billing_type"] = selection_key(
            "ggandy.rent.schedule", f"{kind}_billing_type", row[f"{label}費方式"]
        )
        values[f"{kind}_rate"] = num(row[f"每度{label}價"])
        values[f"{kind}_fixed_fee"] = num(row[f"固定{label}費"])
        values[f"{kind}_prev_reading"] = num(row[f"{label}錶上期度數"])
        current = opt_num(row[f"{label}錶本期度數"])
        if current is not None:
            values[f"{kind}_curr_reading"] = current
    return values


# ----------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------
sections = parse_sections(load_csv_text())
prop_row = sections["物件"][0]
existing = env["ggandy.property"].with_context(active_test=False).search([("name", "=", prop_row["物件名稱"])])
if existing and not ALLOW_DUPLICATE:
    raise SystemExit(
        f"目標資料庫已有同名物件「{prop_row['物件名稱']}」（{', '.join(existing.mapped('code'))}），"
        "為避免重複插入先停下；確定要再建一份請加 ALLOW_DUPLICATE=1"
    )

partners, created_partners = upsert_partners(sections["聯絡人"])
prop, manager = create_property(prop_row, partners)
units = {}
for row in sections["出租單位"]:
    units[row["房號"]] = env["ggandy.property.unit"].create({"property_id": prop.id, **unit_values(row)})

schedule_rows = {}
for row in sections["租金期次"]:
    schedule_rows.setdefault(row["房號"], []).append(row)

leases = env["ggandy.lease"]
sheet_states = {}  # lease id → Sheet 上的狀態
for row in sections["租約"]:
    unit = units[row["房號"]]
    values = {
        "property_id": prop.id,
        "unit_id": unit.id,
        "tenant_id": partners[row["房客"]].id,
        "start_date": to_date(row["起租日"]),
        "end_date": to_date(row["到期日"]),
        "rent_amount": num(row["月租"]),
        "deposit_amount": num(row["押金"]),
        "management_fee": num(row["管理費"]),
        "rent_due_day": int(num(row["繳租日"], 5)),
        "electricity_start_reading": num(row["入住電錶度數"]),
        "water_start_reading": num(row["入住水錶度數"]),
    }
    if not COMMIT:
        values["name"] = f"{DRY_PREFIX}{unit.name}"
    lease = env["ggandy.lease"].create(values)
    lease._generate_rent_schedule()
    sheet_rows = sorted(schedule_rows.get(row["房號"], []), key=lambda item: item["期間起"])
    generated = lease.schedule_ids.sorted("period_start")
    if [str(s.period_start) for s in generated] != [to_date(r["期間起"]).isoformat() for r in sheet_rows]:
        raise SystemExit(
            f"{row['房號']}：系統依租期產生的期次（{len(generated)} 期）和 Sheet 期次（{len(sheet_rows)} 期）對不上，"
            "請檢查起租日／到期日"
        )
    # 由舊到新逐期寫入，上期度數才會一路正確往後帶
    for schedule, sheet_row in zip(generated, sheet_rows):
        schedule.write(schedule_values(sheet_row))
    leases |= lease
    sheet_states[lease.id] = selection_key("ggandy.lease", "state", row["狀態"]) if row["狀態"] else "draft"

# 生效：和 action_activate 一樣改狀態、刷新單位狀態，但只替這個物件建帳單
# （action_activate 會呼叫全域 _cron_create_due_invoices，順便替其他物件建帳單）
today = date.today()
for lease in leases:
    target = sheet_states[lease.id]
    if target == "active":
        lease.write({"state": "active"})
    elif target != "draft":
        warnings.append(f"{lease.unit_id.name}：Sheet 狀態「{target}」不自動處理，租約維持草稿")
units_records = env["ggandy.property.unit"].browse([unit.id for unit in units.values()])
units_records._refresh_from_active_leases()

invoice_count = 0
if CREATE_INVOICES:
    for schedule in leases.filtered(lambda item: item.state == "active").schedule_ids.sorted("period_start"):
        if not schedule.invoice_id and schedule.period_start <= today and schedule.total_amount > 0:
            schedule.action_create_invoice()
            invoice_count += 1
env.flush_all()

# ----------------------------------------------------------------------
# 逐欄核對
# ----------------------------------------------------------------------
for row in sections["聯絡人"]:
    partner = partners[row["名稱"]]
    label = f"[聯絡人 {row['名稱']}]"
    check(label, "名稱", partner.name, row["名稱"])
    for column, field in (("電話", "phone"), ("Email", "email"), ("內部參考", "ref")):
        if partner in created_partners or row[column]:
            check(label, column, partner[field], row[column])
    if partner in created_partners:
        check(label, "備註", plain(partner.comment), row["備註"])
    check(label, "身分-房東", partner.is_ggandy_owner, "房東" in row["身分"])
    check(label, "身分-房客", partner.is_ggandy_tenant, "房客" in row["身分"])

label = f"[物件 {prop.name}]"
for column, field in (("物件名稱", "name"), ("街道", "street"), ("街道2", "street2"), ("城市", "city"), ("郵遞區號", "zip")):
    check(label, column, prop[field], prop_row[column])
check(label, "物件類型", prop.property_type, selection_key("ggandy.property", "property_type", prop_row["物件類型"]))
check(label, "管理模式", prop.management_mode, selection_key("ggandy.property", "management_mode", prop_row["管理模式"]))
check(label, "房東", prop.owner_id.name, prop_row["房東"])
check(label, "國家", prop.country_id.code, prop_row["國家"])
check(label, "取得日期", prop.acquisition_date, to_date(prop_row["取得日期"]))
check(label, "管理到期日", prop.management_end_date, to_date(prop_row["管理到期日"]))
check(label, "備註", plain(prop.note), prop_row["備註"])
if manager:
    check(label, "負責人", prop.manager_id.name, manager.name)

for row in sections["出租單位"]:
    unit = units[row["房號"]]
    label = f"[單位 {row['房號']}]"
    for field, value in unit_values(row).items():
        check(label, field, unit[field], value, numeric=isinstance(value, float))
    if row["狀態（核對用）"]:
        check(label, "狀態", unit.state, selection_key("ggandy.property.unit", "state", row["狀態（核對用）"]))

for row in sections["租約"]:
    lease = leases.filtered(lambda item: item.unit_id == units[row["房號"]])
    label = f"[租約 {row['房號']}]"
    check(label, "房客", lease.tenant_id.name, partners[row["房客"]].name)
    check(label, "起租日", lease.start_date, to_date(row["起租日"]))
    check(label, "到期日", lease.end_date, to_date(row["到期日"]))
    for column, field in (("月租", "rent_amount"), ("押金", "deposit_amount"), ("管理費", "management_fee"),
                          ("繳租日", "rent_due_day"), ("入住電錶度數", "electricity_start_reading"),
                          ("入住水錶度數", "water_start_reading")):
        check(label, column, lease[field], num(row[column]), numeric=True)
    check(label, "狀態", lease.state, sheet_states[lease.id])
    check(label, "期次數", len(lease.schedule_ids), len(schedule_rows.get(row["房號"], [])), numeric=True)

checked_schedules = 0
for room, rows in schedule_rows.items():
    lease = leases.filtered(lambda item: item.unit_id == units[room])
    by_start = {str(s.period_start): s for s in lease.schedule_ids}
    for row in rows:
        schedule = by_start.get(to_date(row["期間起"]).isoformat())
        label = f"[期次 {room} {row['帳單月份']}]"
        if not schedule:
            diffs.append(f"{label} 找不到期次")
            continue
        checked_schedules += 1
        check(label, "期間起", schedule.period_start, to_date(row["期間起"]))
        for field, value in schedule_values(row).items():
            check(label, field, schedule[field], value, numeric=isinstance(value, float))
        if not opt_num(row["電錶本期度數"]):
            check(label, "electricity_curr_reading", schedule.electricity_curr_reading, 0.0, numeric=True)
        for column, field in (("用電度數（核對用）", "electricity_usage"), ("電費（核對用）", "electricity_amount"),
                              ("用水度數（核對用）", "water_usage"), ("水費（核對用）", "water_amount"),
                              ("應收合計（核對用）", "total_amount")):
            if row.get(column):
                check(label, column, schedule[field], num(row[column]), numeric=True)
        if schedule.invoice_id:
            check(label, "草稿帳單金額", schedule.invoice_id.amount_total, schedule.total_amount, numeric=True)

# ----------------------------------------------------------------------
# 結果
# ----------------------------------------------------------------------
print(f"物件：{prop.name}（{prop.code}）")
print(f"聯絡人：新增 {len(created_partners)}、沿用 {len(partners) - len(created_partners)}")
print(f"出租單位 {len(units)}、租約 {len(leases)}（生效 {len(leases.filtered(lambda item: item.state == 'active'))}）、"
      f"期次 {len(leases.schedule_ids)}（核對 {checked_schedules}）、草稿帳單 {invoice_count}")
print("單位狀態：", "、".join(f"{u.name}={dict(u._fields['state'].selection)[u.state]}" for u in units.values()))
for message in warnings:
    print("⚠", message)
print(f"核對差異 {len(diffs)} 筆")
for message in diffs:
    print("  ✗", message)

if COMMIT and not diffs:
    env.cr.commit()
    print("已寫入（COMMIT）")
else:
    env.cr.rollback()
    if COMMIT:
        print("有差異，未寫入，已還原")
    else:
        print("試跑，已還原；差異 0 再加 COMMIT=1 執行")
