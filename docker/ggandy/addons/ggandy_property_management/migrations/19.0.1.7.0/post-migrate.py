from odoo import SUPERUSER_ID, api

PRODUCT_XMLIDS = [
    "ggandy_property_management.product_product_rent",
    "ggandy_property_management.product_product_management_fee",
    "ggandy_property_management.product_product_electricity",
    "ggandy_property_management.product_product_water",
    "ggandy_property_management.product_product_owner_settlement",
]


def migrate(cr, version):
    """GGAndy 金額一律未稅：product_data.xml 是 noupdate，既有資料庫要在這裡清掉產品上的銷項／進項稅。"""
    env = api.Environment(cr, SUPERUSER_ID, {})
    for xmlid in PRODUCT_XMLIDS:
        product = env.ref(xmlid, raise_if_not_found=False)
        if product:
            product.write({"taxes_id": [(5, 0, 0)], "supplier_taxes_id": [(5, 0, 0)]})
