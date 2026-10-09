# 把一個「測試用」物件清回沒有房客的狀態，方便重複演示「從 Google Sheet 匯入租約與抄表」。
#
# 會刪除：該物件所有租約、租金期次、租金帳單（含已過帳：先取消收款、退回草稿再刪，發票號碼會跳號）、
#         這些租約上名稱以 DEMO_PREFIX 開頭的房客；並把出租單位狀態改回空房。
# 不會動：物件、出租單位與水電設定、整備專案、銷售／採購單、庫存位置、其他物件的資料。
#
# 預設只試跑（最後 rollback），確認輸出後加 COMMIT=1 才真的寫入：
#   docker exec -i ggandy_odoo19_web sh -c 'odoo shell -c /etc/odoo/odoo.conf -d ggandy_dev \
#     --db_host="$HOST" --db_port="$PORT" --db_user="$POSTGRES_USER" --db_password="$POSTGRES_PASSWORD" \
#     --no-http --log-level=error' < notebook/scripts/reset_property_tenants.py
#   docker exec -i -e COMMIT=1 ggandy_odoo19_web sh -c '...同上...' < notebook/scripts/reset_property_tenants.py
#
# 只能用在測試資料！預設物件是 PROP/2026/0134（大安羅斯福路公館）。
import os

PROPERTY_CODE = os.environ.get("PROPERTY_CODE", "PROP/2026/0134")
DEMO_PREFIX = os.environ.get("DEMO_PREFIX", "demo-")
COMMIT = os.environ.get("COMMIT") == "1"

prop = env["ggandy.property"].search([("code", "=", PROPERTY_CODE)])
if not prop:
    raise SystemExit(f"找不到物件 {PROPERTY_CODE}")

leases = env["ggandy.lease"].with_context(active_test=False).search([("property_id", "=", prop.id)])
invoices = env["account.move"].search([("ggandy_lease_id", "in", leases.ids)])
posted = invoices.filtered(lambda move: move.state == "posted")
payments = env["account.payment"].search([("reconciled_invoice_ids", "in", posted.ids)]) if posted else env["account.payment"]
tenants = (leases.mapped("tenant_id") | leases.mapped("co_tenant_ids")).filtered(
    lambda partner: partner.name.startswith(DEMO_PREFIX)
)
print(
    f"{prop.name}（{PROPERTY_CODE}）：租約 {len(leases)}、期次 {len(leases.schedule_ids)}、"
    f"帳單 {len(invoices)}（已過帳 {len(posted)}）、收款 {len(payments)}、{DEMO_PREFIX} 房客 {len(tenants)}"
)

payments.action_draft()
payments.with_context(force_delete=True).unlink()
posted.button_draft()
invoices.with_context(force_delete=True).unlink()
leases.write({"state": "cancelled"})
leases.unlink()  # 期次 ondelete=cascade
still_used = tenants.filtered(
    lambda partner: env["ggandy.lease"].with_context(active_test=False).search_count([("tenant_id", "=", partner.id)])
)
(tenants - still_used).unlink()
prop.unit_ids._refresh_from_active_leases()

print("清空後：租約", env["ggandy.lease"].with_context(active_test=False).search_count([("property_id", "=", prop.id)]),
      "｜單位狀態", sorted(set(prop.unit_ids.mapped("state"))))
if COMMIT:
    env.cr.commit()
    print("已寫入（COMMIT）")
else:
    env.cr.rollback()
    print("試跑，已還原；確認後加 COMMIT=1 再執行")
