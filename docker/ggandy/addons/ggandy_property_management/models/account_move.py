from odoo import fields, models


class AccountMove(models.Model):
    _inherit = "account.move"

    ggandy_lease_id = fields.Many2one(
        "ggandy.lease",
        string="GGAndy 租約",
        copy=False,
        index=True,
        ondelete="restrict",
        help="這張客戶發票對應的 GGAndy 租約。租金期次建立帳單時會自動填入；若手動開單，請記得補上，報表才串得起來。",
    )
    ggandy_property_id = fields.Many2one(
        "ggandy.property",
        related="ggandy_lease_id.property_id",
        string="GGAndy 物件",
        store=True,
        readonly=True,
        help="由 GGAndy 租約自動帶出的物件，用於查詢與報表。它是跟著租約走的影子欄位，不用手動改。",
    )
    ggandy_unit_id = fields.Many2one(
        "ggandy.property.unit",
        related="ggandy_lease_id.unit_id",
        string="GGAndy 出租單位",
        store=True,
        readonly=True,
        help="由 GGAndy 租約自動帶出的出租單位。若這裡空白，先檢查帳單有沒有連到租約。",
    )
    ggandy_owner_contract_id = fields.Many2one(
        "ggandy.owner.contract",
        string="GGAndy 房東合約",
        copy=False,
        index=True,
        ondelete="restrict",
        help="這張 Vendor Bill 對應的房東合約。房東自動結算會填入它，帳務總表也靠它找房東應付。",
    )
    ggandy_owner_property_id = fields.Many2one(
        "ggandy.property",
        related="ggandy_owner_contract_id.property_id",
        string="GGAndy 房東結算物件",
        store=True,
        readonly=True,
        help="由房東合約自動帶出的結算物件。用來把房東帳單歸到正確物件，月底對帳比較輕鬆。",
    )
    ggandy_expense_property_id = fields.Many2one(
        "ggandy.property",
        string="GGAndy 費用歸屬物件",
        copy=False,
        index=True,
        ondelete="restrict",
        help="用於 GGAndy 帳務總表歸集一般採購費用或員工代墊費用。這格沒填，總表不容易歸到正確物件。",
    )
    ggandy_expense_unit_id = fields.Many2one(
        "ggandy.property.unit",
        string="GGAndy 費用歸屬單位",
        copy=False,
        index=True,
        ondelete="restrict",
        domain="[('property_id', '=', ggandy_expense_property_id)]",
        help="若費用可歸屬到特定出租單位，請填此欄位以便帳務總表精準呈現。能填到單位就不要只停在物件層級。",
    )
    ggandy_expense_kind = fields.Selection(
        [
            ("purchase", "採購費用"),
            ("employee_advance", "公司員工代墊"),
        ],
        string="GGAndy 費用類型",
        copy=False,
        help="標記這筆採購帳單是一般採購費用，還是公司員工代墊。帳務總表會分欄呈現，對帳時比較清楚。",
    )
    ggandy_settlement_period_start = fields.Date(
        string="GGAndy 結算開始",
        copy=False,
        index=True,
        help="房東結算 Vendor Bill 對應期間的起始日，通常是該月 1 號。自動建立時會寫入，方便總表抓同一月份。",
    )
    ggandy_settlement_period_end = fields.Date(
        string="GGAndy 結算結束",
        copy=False,
        index=True,
        help="房東結算 Vendor Bill 對應期間的結束日，通常是月底。它跟開始日一起把這張帳單釘在正確月份上。",
    )
