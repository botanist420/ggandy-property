from odoo import fields, models


class GgandyPropertyUnit(models.Model):
    _inherit = "ggandy.property.unit"

    stock_location_id = fields.Many2one(
        "stock.location",
        string="庫存位置",
        copy=False,
        readonly=True,
        ondelete="set null",
        help="這個房間在倉庫中的位置，收貨直接入房、內部調撥、房間物品清單都用它。"
        "在物件按「開始整備」或「同步庫存位置」時自動建立。",
    )
