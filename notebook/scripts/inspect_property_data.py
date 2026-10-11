# 盤點「名稱含 PROPERTY_NAME」的物件底下有哪些資料（只讀不寫），插入資料快照前先確認目標資料庫的舊資料。
# 說明文件：notebook/docs/property-snapshot.md
#
#   docker exec -i -e PROPERTY_NAME=大安公館 <odoo容器> sh -c 'odoo shell -c /etc/odoo/odoo.conf -d <資料庫> \
#     --db_host="$HOST" --db_port="$PORT" --db_user="$POSTGRES_USER" --db_password="$POSTGRES_PASSWORD" \
#     --no-http --log-level=error' < notebook/scripts/inspect_property_data.py
import os
from collections import Counter

PROPERTY_NAME = os.environ.get("PROPERTY_NAME", "大安公館")

props = env["ggandy.property"].with_context(active_test=False).search([("name", "ilike", PROPERTY_NAME)])
print(f"名稱含「{PROPERTY_NAME}」的物件：{len(props)} 筆")


def count_by(records, field):
    return "、".join(f"{key or '（空）'} {n}" for key, n in Counter(records.mapped(field)).items()) or "無"


for prop in props:
    print(f"\n=== {prop.name}（{prop.code}，id {prop.id}，{'使用中' if prop.active else '已封存'}）")
    print(f"管理模式 {prop.management_mode}｜房東 {prop.owner_id.name or '-'}｜負責人 {prop.manager_id.name or '-'}")
    units = env["ggandy.property.unit"].with_context(active_test=False).search([("property_id", "=", prop.id)])
    print(f"出租單位 {len(units)}：" + "、".join(f"{u.name}({u.state})" for u in units))
    leases = env["ggandy.lease"].with_context(active_test=False).search([("property_id", "=", prop.id)])
    print(f"租約 {len(leases)}（{count_by(leases, 'state')}）")
    for lease in leases:
        print(f"  {lease.name} {lease.unit_id.name} {lease.tenant_id.name}（partner id {lease.tenant_id.id}，"
              f"電話 {lease.tenant_id.phone or '-'}）{lease.start_date}～{lease.end_date} {lease.state} 期次 {len(lease.schedule_ids)}")
    invoices = env["account.move"].search([("ggandy_lease_id", "in", leases.ids)])
    print(f"租金帳單 {len(invoices)}（狀態：{count_by(invoices, 'state')}；付款：{count_by(invoices, 'payment_state')}）")
    posted = invoices.filtered(lambda move: move.state == "posted")
    if posted:
        payments = env["account.payment"].search([("reconciled_invoice_ids", "in", posted.ids)])
        print(f"  已過帳：{', '.join(posted.mapped('name'))}｜相關收款 {len(payments)}")
    contracts = env["ggandy.owner.contract"].with_context(active_test=False).search([("property_id", "=", prop.id)])
    print(f"房東合約 {len(contracts)}（{count_by(contracts, 'state')}）")
    if contracts:
        bills = env["account.move"].search([("ggandy_owner_contract_id", "in", contracts.ids)])
        print(f"  房東帳單 {len(bills)}（{count_by(bills, 'state')}）")
    requests_ = env["ggandy.maintenance.request"].search([("property_id", "=", prop.id)])
    print(f"報修單 {len(requests_)}")
    if "preparation_project_id" in prop._fields and prop.preparation_project_id:
        project = prop.preparation_project_id
        sale_orders = env["sale.order"].search([("project_id", "=", project.id)]) if "project_id" in env["sale.order"]._fields else []
        purchase_orders = (
            env["purchase.order"].search([("project_id", "=", project.id)])
            if "project_id" in env["purchase.order"]._fields
            else []
        )
        print(f"整備專案 {project.name}（任務 {project.task_count}）｜銷售單 {len(sale_orders)}｜採購單 {len(purchase_orders)}")
    if "stock_location_id" in prop._fields and prop.stock_location_id:
        print(f"庫存位置 {prop.stock_location_id.complete_name}")
    attachments = env["ir.attachment"].search_count(
        ["|", "&", ("res_model", "=", "ggandy.property"), ("res_id", "=", prop.id),
         "&", ("res_model", "=", "ggandy.property.unit"), ("res_id", "in", units.ids)]
    )
    print(f"物件／單位附件 {attachments}")

env.cr.rollback()
