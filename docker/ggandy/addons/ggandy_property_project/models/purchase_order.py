from odoo import api, fields, models


class PurchaseOrder(models.Model):
    _inherit = "purchase.order"

    ggandy_is_preparation = fields.Boolean(
        string="整備採購",
        compute="_compute_ggandy_is_preparation",
        help="專案是某個物件的整備專案時會勾起來，供應商就只能選聯絡人裡標記為「合作廠商」的人；"
        "在供應商欄直接輸入新名字建立的聯絡人會自動標記為合作廠商。",
    )

    @api.depends("project_id")
    def _compute_ggandy_is_preparation(self):
        # 採購人員不一定有物件的讀取權限，只拿來判斷專案是不是整備專案
        preparation_projects = self.env["ggandy.property"].sudo().search(
            [("preparation_project_id", "in", self.project_id.ids)]
        ).preparation_project_id
        for order in self:
            order.ggandy_is_preparation = order.project_id in preparation_projects
