from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    """GGAndy 金額一律未稅：包租服務費不帶公司預設的銷項／進項稅。"""
    env = api.Environment(cr, SUPERUSER_ID, {})
    product = env.ref(
        "ggandy_property_project.product_product_master_lease_service_fee",
        raise_if_not_found=False,
    )
    if product:
        product.write({"taxes_id": [(5, 0, 0)], "supplier_taxes_id": [(5, 0, 0)]})
