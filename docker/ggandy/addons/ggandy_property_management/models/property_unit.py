from odoo import api, fields, models
from odoo.exceptions import ValidationError


class GgandyPropertyUnit(models.Model):
    _name = "ggandy.property.unit"
    _description = "出租單位"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "property_id, floor, name"

    name = fields.Char(string="房號／單位名稱", required=True, tracking=True)
    active = fields.Boolean(default=True)
    property_id = fields.Many2one(
        "ggandy.property",
        string="所屬物件",
        required=True,
        ondelete="restrict",
        tracking=True,
        index=True,
    )
    company_id = fields.Many2one(
        "res.company",
        related="property_id.company_id",
        store=True,
        readonly=True,
        index=True,
    )
    currency_id = fields.Many2one(
        "res.currency",
        related="company_id.currency_id",
        store=True,
        readonly=True,
    )
    unit_type = fields.Selection(
        [
            ("suite", "套房"),
            ("room", "雅房"),
            ("whole", "整層／整戶"),
            ("shop", "店面"),
            ("office", "辦公室"),
            ("parking", "車位"),
            ("other", "其他"),
        ],
        string="單位類型",
        required=True,
        default="suite",
        tracking=True,
    )
    floor = fields.Char(string="樓層")
    area = fields.Float(string="坪數")
    bedroom_count = fields.Integer(string="房間數")
    bathroom_count = fields.Integer(string="衛浴數")
    monthly_rent = fields.Monetary(string="參考月租", tracking=True)
    deposit_months = fields.Float(string="押金月數", default=2.0)
    state = fields.Selection(
        [
            ("vacant", "空房"),
            ("reserved", "已保留"),
            ("occupied", "已出租"),
            ("maintenance", "維修中"),
            ("inactive", "停用"),
        ],
        string="出租狀態",
        required=True,
        default="vacant",
        tracking=True,
    )
    lease_ids = fields.One2many("ggandy.lease", "unit_id", string="租約紀錄")
    maintenance_request_ids = fields.One2many(
        "ggandy.maintenance.request", "unit_id", string="報修紀錄"
    )
    lease_count = fields.Integer(compute="_compute_counts")
    maintenance_count = fields.Integer(compute="_compute_counts")
    note = fields.Html(string="單位備註")

    @api.depends("property_id.name", "name")
    def _compute_display_name(self):
        for record in self:
            record.display_name = (
                f"{record.property_id.name} / {record.name}"
                if record.property_id
                else record.name
            )

    @api.depends("lease_ids", "maintenance_request_ids")
    def _compute_counts(self):
        for record in self:
            record.lease_count = len(record.lease_ids)
            record.maintenance_count = len(record.maintenance_request_ids)

    @api.constrains("area", "monthly_rent", "deposit_months")
    def _check_non_negative_values(self):
        for record in self:
            if record.area < 0 or record.monthly_rent < 0 or record.deposit_months < 0:
                raise ValidationError("坪數、租金與押金月數不可小於零。")

    def _refresh_from_active_leases(self):
        Lease = self.env["ggandy.lease"]
        for record in self:
            has_active_lease = Lease.search_count(
                [("unit_id", "=", record.id), ("state", "=", "active")]
            )
            if record.state in ("vacant", "occupied"):
                record.state = "occupied" if has_active_lease else "vacant"

    def action_view_leases(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "租約紀錄",
            "res_model": "ggandy.lease",
            "view_mode": "list,form",
            "domain": [("unit_id", "=", self.id)],
            "context": {
                "default_property_id": self.property_id.id,
                "default_unit_id": self.id,
                "default_rent_amount": self.monthly_rent,
                "default_deposit_amount": self.monthly_rent * self.deposit_months,
            },
        }

    def action_view_maintenance(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "報修紀錄",
            "res_model": "ggandy.maintenance.request",
            "view_mode": "list,form",
            "domain": [("unit_id", "=", self.id)],
            "context": {
                "default_property_id": self.property_id.id,
                "default_unit_id": self.id,
            },
        }
