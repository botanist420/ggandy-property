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

