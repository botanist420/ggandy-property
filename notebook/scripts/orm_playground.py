# ============================================================
# GGAndy Odoo 19 資料關係 Playground（Odoo shell 版）
# 由 2026-09-15_playground.ipynb 改寫；純查詢，不 create / write / unlink。
#
# 執行（在 repo 根目錄，腳本從主機 stdin 餵進容器，不需要掛載 notebook/）：
#   docker exec -i ggandy_odoo19_web sh -c 'odoo shell -c /etc/odoo/odoo.conf -d ggandy_dev \
#     --db_host="$HOST" --db_port="$PORT" --db_user="$POSTGRES_USER" --db_password="$POSTGRES_PASSWORD" \
#     --no-http --log-level=warn' < notebook/scripts/orm_playground.py
#
# 整體關係：
#
#   房東 res.partner
#          ↑ owner_id（Many2one）
#   物件 ggandy.property
#          ↓ unit_ids（One2many）
#   出租單位 ggandy.property.unit
#          ↓ lease_ids（One2many）
#   租約 ggandy.lease ── tenant_id（Many2one）──→ 房客 res.partner
#          ↓ schedule_ids（One2many）
#   租金期次 ggandy.rent.schedule ── invoice_id（Many2one）──→ Odoo 發票
#
# 一棟物件可以有很多出租單位；一個出租單位可以累積很多歷史租約；
# 每張租約可以產生很多個月的租金期次。
# ============================================================

# 讓 Pylance 知道 env 的型別；Odoo shell 執行時會提供真正的 env。
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from odoo.api import Environment
    env = cast(Environment, None)


print(f"已連線：{env.cr.dbname}")
print(f"公司：{env.company.name}")


# ============================================================
# 1. Odoo ORM 的三種常見關係
#
# - Many2one：目前這筆資料只指向一筆，例如出租單位的 property_id 指向一棟物件。
# - One2many：從另一側看回來的多筆資料，例如物件的 unit_ids 是它底下所有出租單位。
# - Many2many：兩邊都可以有多筆，例如物件的 co_owner_ids 可以有多位共同屋主。
#
# One2many 通常不是自己存一串 ID；真正存放關係的是另一個模型上的 Many2one。
# 例如 ggandy_property_unit 資料表存有 property_id。
# ============================================================

print("\n[1] 資料筆數")
models_to_count = {
    "物件": "ggandy.property",
    "出租單位": "ggandy.property.unit",
    "租約": "ggandy.lease",
    "租金期次": "ggandy.rent.schedule",
}
for label, model_name in models_to_count.items():
    print(f"{label}：{env[model_name].search_count([])} 筆")


# ============================================================
# 2. 從物件找到出租單位（One2many：property.unit_ids）
# ============================================================

print("\n[2] 物件 → 出租單位")
properties = env["ggandy.property"].search([], order="code, name")
for property_record in properties:
    if not property_record.unit_ids:
        continue
    print(f"\n🏠 {property_record.code}｜{property_record.name}")
    print(f"   房東：{property_record.owner_id.name}")
    print(f"   地址：{property_record.address_display}")
    print(f"   出租單位：{len(property_record.unit_ids)} 間")
    for unit in property_record.unit_ids:
        print(f"   └─ {unit.name}｜{unit.area:g} 坪｜月租 {unit.monthly_rent:,.0f}｜{unit.state}")
print(f"\n（共 {len(properties)} 個物件，只列出有出租單位的）")


# ============================================================
# 3. 從出租單位反查物件（Many2one：unit.property_id）
# 和上一段是同一段關係的兩個方向。
# ============================================================

print("\n[3] 出租單位 → 物件")
units = env["ggandy.property.unit"].search([], order="property_id, floor, name")
for unit in units:
    print(f"{unit.display_name} → 所屬物件：{unit.property_id.name}｜歷史租約：{len(unit.lease_ids)} 張")


# ============================================================
# 4. 租約把出租單位與房客接起來
# 租約生效時，系統會把出租單位改成「已出租」，並依月份建立 schedule_ids。
# ============================================================

print("\n[4] 租約")
leases = env["ggandy.lease"].search([], order="start_date desc")
if not leases:
    print("目前沒有租約資料。之後在 Odoo 建立租約，再重跑就能看到關係。")
for lease in leases:
    print(f"\n📄 {lease.name}｜{lease.state}")
    print(f"   物件：{lease.property_id.name}")
    print(f"   單位：{lease.unit_id.name}")
    print(f"   房客：{lease.tenant_id.name}")
    print(f"   租期：{lease.start_date} ～ {lease.end_date}")
    print(f"   月租：{lease.rent_amount:,.0f}｜租金期次：{len(lease.schedule_ids)} 筆")

print("\n[4-1] 最近 12 筆租金期次")
schedules = env["ggandy.rent.schedule"].search([], order="due_date desc", limit=12)
if not schedules:
    print("目前沒有租金期次。租約生效後可以自動產生。")
for schedule in schedules:
    print(
        f"{schedule.name}｜{schedule.tenant_id.name}｜"
        f"應收 {schedule.total_amount:,.0f}｜到期 {schedule.due_date}｜{schedule.collection_state}"
    )


# ============================================================
# 5. 兩個很好用的 Recordset 操作
# - filtered()：從查到的紀錄中篩選，例如只留下空房。
# - mapped()：沿欄位關係取值，例如從所有單位取出它們所屬的物件。
# ============================================================

print("\n[5] filtered / mapped")
vacant_units = units.filtered(lambda unit: unit.state == "vacant")
related_properties = units.mapped("property_id")
print("空房：", vacant_units.mapped("display_name"))
print("這些單位涉及的物件：", related_properties.mapped("name"))


# ============================================================
# 6. 結束
# Odoo shell 從 stdin 執行完不會自動 commit，這份腳本也只做查詢。
# 日後練習 create() / write() 時：想保留異動才 env.cr.commit()，只是實驗就 env.cr.rollback()。
# ============================================================

env.cr.rollback()
print("\n已 rollback，沒有寫入任何資料。")
