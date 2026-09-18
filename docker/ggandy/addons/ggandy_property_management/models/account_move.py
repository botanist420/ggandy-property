from odoo import fields, models


class AccountMove(models.Model):
    _inherit = "account.move"

    ggandy_lease_id = fields.Many2one(
        "ggandy.lease",
        string="GGAndy 租約",
        copy=False,
        index=True,
        ondelete="restrict",
    )
    ggandy_property_id = fields.Many2one(
        "ggandy.property",
        related="ggandy_lease_id.property_id",
        string="GGAndy 物件",
        store=True,
        readonly=True,
    )
    ggandy_unit_id = fields.Many2one(
        "ggandy.property.unit",
        related="ggandy_lease_id.unit_id",
        string="GGAndy 出租單位",
        store=True,
        readonly=True,
    )
    ggandy_owner_contract_id = fields.Many2one(
        "ggandy.owner.contract",
        string="GGAndy 房東合約",
        copy=False,
        index=True,
        ondelete="restrict",
    )
    ggandy_owner_property_id = fields.Many2one(
        "ggandy.property",
        related="ggandy_owner_contract_id.property_id",
        string="GGAndy 房東結算物件",
        store=True,
        readonly=True,
    )
    ggandy_expense_property_id = fields.Many2one(
        "ggandy.property",
        string="GGAndy 費用歸屬物件",
        copy=False,
        index=True,
        ondelete="restrict",
        help="用於 GGAndy 帳務總表歸集一般採購費用或員工代墊費用。",
    )
    ggandy_expense_kind = fields.Selection(
        [
            ("purchase", "採購費用"),
            ("employee_advance", "公司員工代墊"),
        ],
        string="GGAndy 費用類型",
        copy=False,
    )
    ggandy_settlement_period_start = fields.Date(
        string="GGAndy 結算開始",
        copy=False,
        index=True,
    )
    ggandy_settlement_period_end = fields.Date(
        string="GGAndy 結算結束",
        copy=False,
        index=True,
    )
