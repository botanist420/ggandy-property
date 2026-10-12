from odoo import api, fields, models


class GgandyOwnerAdjustment(models.Model):
    _name = "ggandy.owner.adjustment"
    _description = "房東帳單調整項"
    _order = "period_start desc, id desc"

    contract_id = fields.Many2one(
        "ggandy.owner.contract",
        string="房東合約",
        required=True,
        ondelete="cascade",
        index=True,
    )
    property_id = fields.Many2one(
        related="contract_id.property_id",
        string="物件",
        store=True,
    )
    company_id = fields.Many2one(
        related="contract_id.company_id",
        string="公司",
        store=True,
    )
    currency_id = fields.Many2one(
        related="contract_id.currency_id",
        string="幣別",
        store=True,
    )
    period_start = fields.Date(
        string="帳單月份",
        required=True,
        default=lambda self: fields.Date.context_today(self).replace(day=1),
        help="這筆調整要加進哪個月的房東帳單。選該月任何一天都可以，系統會自動存成那個月的 1 號。",
    )
    name = fields.Char(
        string="說明",
        required=True,
        help="會原封不動印在房東帳單上，例如「R2 押金退還」「R8 租金＋管理費扣回」。",
    )
    amount = fields.Monetary(
        string="金額",
        required=True,
        help="正數是多付給房東、負數是從房東帳單扣回。會加進那個月的房東帳單，"
        "按「重新計算房東帳單」也不會被蓋掉；帳單已經過帳才新增的話，重新計算只會告訴你差多少。",
    )
    note = fields.Char(string="備註", help="內部備註，不會出現在房東帳單上。")

    @api.model_create_multi
    def create(self, vals_list):
        for values in vals_list:
            if values.get("period_start"):
                values["period_start"] = fields.Date.to_date(values["period_start"]).replace(day=1)
        return super().create(vals_list)

    def write(self, values):
        if values.get("period_start"):
            values["period_start"] = fields.Date.to_date(values["period_start"]).replace(day=1)
        return super().write(values)
