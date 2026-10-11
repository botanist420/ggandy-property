# 把一個物件「截至目前」的資料匯出成一份分段 CSV（聯絡人、物件、出租單位、租約、租金期次），
# 貼到 Google Sheet 後可以用 import_property_snapshot.py 在另一個資料庫（例如正式機）原樣重建。
# 只讀不寫。CSV 印到 stdout，說明文件見 notebook/docs/property-snapshot.md。
#
#   docker exec -i -e PROPERTY_CODE=PROP/2026/0134 -e READINGS_UNTIL=2026-09 ggandy_odoo19_web sh -c \
#     'odoo shell -c /etc/odoo/odoo.conf -d ggandy_dev --db_host="$HOST" --db_port="$PORT" \
#      --db_user="$POSTGRES_USER" --db_password="$POSTGRES_PASSWORD" --no-http --log-level=error' \
#     < notebook/scripts/export_property_snapshot.py > snapshot.csv
#
# READINGS_UNTIL（YYYY-MM，可省略）：只匯出抄表月份在這個月（含）以前的「本期度數」，
# 之後的期次本期度數留空，避免把測試時亂填的讀數帶到正式機。
import csv
import os
import sys
from datetime import date

PROPERTY_CODE = os.environ.get("PROPERTY_CODE", "PROP/2026/0134")
READINGS_UNTIL = os.environ.get("READINGS_UNTIL")


def label(record, field_name):
    value = record[field_name]
    if not value:
        return ""
    return dict(record._fields[field_name].selection)[value]


def num(value):
    # 注意 0 == False，不能用 `value in (False, None)`；None 代表刻意留空
    if value is None or value is False:
        return ""
    return f"{value:g}" if isinstance(value, float) else str(value)


def day(value):
    return value.strftime("%Y-%m-%d") if value else ""


def text(value):
    if not value:
        return ""
    # html 欄位轉成一行純文字
    from odoo.tools import html2plaintext

    return " ".join(html2plaintext(str(value)).split())


prop = env["ggandy.property"].search([("code", "=", PROPERTY_CODE)])
if not prop:
    raise SystemExit(f"找不到物件 {PROPERTY_CODE}")
units = prop.unit_ids.sorted(lambda unit: (unit.sequence, unit.name))
leases = env["ggandy.lease"].search(
    [("property_id", "=", prop.id), ("state", "in", ("draft", "active"))], order="unit_id, start_date"
)
partners = prop.owner_id | leases.mapped("tenant_id")

rows = [
    [f"{prop.name} 資料快照（{PROPERTY_CODE}，匯出日 {date.today():%Y-%m-%d}）"],
    ["每段第一列是段落名稱、第二列是欄位名稱；匯入腳本：notebook/scripts/import_property_snapshot.py"],
    [],
    ["【聯絡人】"],
    ["名稱", "身分", "電話", "Email", "內部參考", "備註"],
]
for partner in partners:
    roles = [name for flag, name in (("is_ggandy_owner", "房東"), ("is_ggandy_tenant", "房客")) if partner[flag]]
    rows.append([partner.name, "、".join(roles), partner.phone or "", partner.email or "", partner.ref or "", text(partner.comment)])

rows += [
    [],
    ["【物件】"],
    ["物件名稱", "物件類型", "管理模式", "房東", "負責人", "街道", "街道2", "城市", "行政區", "縣市", "郵遞區號", "國家",
     "房東付款日", "網路費", "取得日期", "管理到期日", "備註"],
    [prop.name, label(prop, "property_type"), label(prop, "management_mode"), prop.owner_id.name or "",
     prop.manager_id.name or "", prop.street or "", prop.street2 or "", prop.city or "", prop.city_id.name or "",
     prop.state_id.name or "", prop.zip or "", prop.country_id.code or "", num(prop.owner_payment_day),
     num(prop.internet_fee), day(prop.acquisition_date), day(prop.management_end_date), text(prop.note)],
    [],
    ["【出租單位】"],
    ["房號", "排序", "單位類型", "樓層", "坪數", "房數", "衛浴數", "月租", "押金月數", "電費方式", "每度電價", "固定電費",
     "水費方式", "每度水價", "固定水費", "狀態（核對用）"],
]
for unit in units:
    rows.append([unit.name, num(unit.sequence), label(unit, "unit_type"), unit.floor or "", num(unit.area),
                 num(unit.bedroom_count), num(unit.bathroom_count), num(unit.monthly_rent), num(unit.deposit_months),
                 label(unit, "electricity_billing_type"), num(unit.electricity_rate), num(unit.electricity_fixed_fee),
                 label(unit, "water_billing_type"), num(unit.water_rate), num(unit.water_fixed_fee), label(unit, "state")])

rows += [
    [],
    ["【租約】"],
    ["房號", "房客", "起租日", "到期日", "月租", "押金", "管理費", "繳租日", "入住電錶度數", "入住水錶度數", "狀態"],
]
for lease in leases:
    rows.append([lease.unit_id.name, lease.tenant_id.name, day(lease.start_date), day(lease.end_date),
                 num(lease.rent_amount), num(lease.deposit_amount), num(lease.management_fee), num(lease.rent_due_day),
                 num(lease.electricity_start_reading), num(lease.water_start_reading), label(lease, "state")])

rows += [
    [],
    ["【租金期次】"],
    ["房號", "帳單月份", "期間起", "期間迄", "繳款期限", "租金", "管理費", "電費方式", "每度電價", "固定電費",
     "電錶上期度數", "電錶本期度數", "用電度數（核對用）", "電費（核對用）", "水費方式", "每度水價", "固定水費",
     "水錶上期度數", "水錶本期度數", "用水度數（核對用）", "水費（核對用）", "應收合計（核對用）", "備註"],
]
for lease in leases:
    for schedule in lease.schedule_ids.sorted("period_start"):
        keep_reading = not READINGS_UNTIL or schedule.meter_month <= READINGS_UNTIL
        utility = {}
        for kind in ("electricity", "water"):
            if keep_reading or schedule[f"{kind}_billing_type"] != "metered":
                utility[kind] = (schedule[f"{kind}_curr_reading"], schedule[f"{kind}_usage"], schedule[f"{kind}_amount"])
            else:
                # 本期度數留空 = 還沒抄表，插入後系統算出的用量與金額都是 0
                utility[kind] = (None, 0.0, 0.0)
        total = schedule.rent_amount + schedule.management_fee + utility["electricity"][2] + utility["water"][2]
        rows.append([
            lease.unit_id.name, schedule.period_month, day(schedule.period_start), day(schedule.period_end),
            day(schedule.due_date), num(schedule.rent_amount), num(schedule.management_fee),
            label(schedule, "electricity_billing_type"), num(schedule.electricity_rate),
            num(schedule.electricity_fixed_fee), num(schedule.electricity_prev_reading),
            num(utility["electricity"][0]), num(utility["electricity"][1]), num(utility["electricity"][2]),
            label(schedule, "water_billing_type"), num(schedule.water_rate), num(schedule.water_fixed_fee),
            num(schedule.water_prev_reading), num(utility["water"][0]), num(utility["water"][1]),
            num(utility["water"][2]), num(total), schedule.note or "",
        ])

csv.writer(sys.stdout, lineterminator="\n").writerows(rows)
