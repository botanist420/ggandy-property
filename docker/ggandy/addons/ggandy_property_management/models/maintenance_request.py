from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError


class GgandyMaintenanceRequest(models.Model):
    _name = "ggandy.maintenance.request"
    _description = "房屋報修單"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "priority desc, request_date desc, id desc"

    name = fields.Char(
        string="報修單號",
        required=True,
        copy=False,
        readonly=True,
        default="New",
        index=True,
    )
    title = fields.Char(string="報修主旨", required=True, tracking=True)
    active = fields.Boolean(default=True)
    state = fields.Selection(
        [
            ("new", "待處理"),
            ("assigned", "已指派"),
            ("in_progress", "處理中"),
            ("waiting", "等待料件／回覆"),
            ("done", "已完成"),
            ("cancelled", "已取消"),
        ],
        string="狀態",
        default="new",
        required=True,
        copy=False,
        tracking=True,
    )
    priority = fields.Selection(
        [("0", "一般"), ("1", "重要"), ("2", "緊急"), ("3", "非常緊急")],
        string="優先度",
        default="0",
        tracking=True,
    )
    category = fields.Selection(
        [
            ("plumbing", "水管／衛浴"),
            ("electric", "電力／照明"),
            ("appliance", "家電"),
            ("air_conditioning", "冷氣"),
            ("furniture", "家具"),
            ("door_lock", "門窗／鎖具"),
            ("cleaning", "清潔／消毒"),
            ("structure", "建物結構"),
            ("other", "其他"),
        ],
        string="維修類型",
        required=True,
        default="other",
        tracking=True,
    )
    property_id = fields.Many2one(
        "ggandy.property",
        string="物件",
        required=True,
        ondelete="restrict",
        tracking=True,
        index=True,
    )
    unit_id = fields.Many2one(
        "ggandy.property.unit",
        string="出租單位",
        ondelete="restrict",
        tracking=True,
        domain="[('property_id', '=', property_id)]",
    )
    lease_id = fields.Many2one(
        "ggandy.lease",
        string="相關租約",
        ondelete="set null",
        domain="[('unit_id', '=', unit_id)]",
    )
    tenant_id = fields.Many2one(
        "res.partner",
        string="報修房客",
        ondelete="set null",
        tracking=True,
    )
    reported_by_id = fields.Many2one(
        "res.partner",
        string="通報人",
        ondelete="set null",
    )
    request_date = fields.Datetime(
        string="通報時間",
        required=True,
        default=fields.Datetime.now,
        tracking=True,
    )
    scheduled_date = fields.Datetime(string="預約處理時間", tracking=True)
    completed_date = fields.Datetime(string="完成時間", copy=False, readonly=True)
    user_id = fields.Many2one(
        "res.users",
        string="內部負責人",
        default=lambda self: self.env.user,
        ondelete="set null",
        tracking=True,
    )
    vendor_id = fields.Many2one(
        "res.partner",
        string="維修廠商",
        ondelete="restrict",
        tracking=True,
        domain="[('is_ggandy_vendor', '=', True)]",
    )
    description = fields.Html(string="問題說明", required=True)
    resolution = fields.Html(string="處理結果")
    estimated_cost = fields.Monetary(string="預估費用", tracking=True)
    actual_cost = fields.Monetary(string="實際費用", tracking=True)
    charged_to = fields.Selection(
        [
            ("company", "管理公司負擔"),
            ("owner", "房東負擔"),
            ("tenant", "房客負擔"),
            ("pending", "待確認"),
        ],
        string="費用歸屬",
        default="pending",
        required=True,
        tracking=True,
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

    @api.model_create_multi
    def create(self, vals_list):
        for values in vals_list:
            if values.get("name", "New") == "New":
                values["name"] = self.env["ir.sequence"].next_by_code(
                    "ggandy.maintenance.request"
                ) or "New"
        records = super().create(vals_list)
        records.mapped("vendor_id").write({"is_ggandy_vendor": True})
        return records

    def write(self, values):
        result = super().write(values)
        if "vendor_id" in values:
            self.mapped("vendor_id").write({"is_ggandy_vendor": True})
        return result

    @api.onchange("property_id")
    def _onchange_property_id(self):
        if self.unit_id and self.unit_id.property_id != self.property_id:
            self.unit_id = False
            self.lease_id = False

    @api.onchange("unit_id")
    def _onchange_unit_id(self):
        if not self.unit_id:
            return
        self.property_id = self.unit_id.property_id
        active_lease = self.env["ggandy.lease"].search(
            [("unit_id", "=", self.unit_id.id), ("state", "=", "active")],
            limit=1,
        )
        self.lease_id = active_lease
        self.tenant_id = active_lease.tenant_id
        if not self.reported_by_id:
            self.reported_by_id = active_lease.tenant_id

    @api.constrains("estimated_cost", "actual_cost")
    def _check_costs(self):
        for record in self:
            if record.estimated_cost < 0 or record.actual_cost < 0:
                raise ValidationError("維修費用不可小於零。")

    def action_assign(self):
        for record in self:
            if not record.user_id and not record.vendor_id:
                raise UserError("請先設定內部負責人或維修廠商。")
            record.state = "assigned"
        return True

    def action_start(self):
        self.write({"state": "in_progress"})
        return True

    def action_wait(self):
        self.write({"state": "waiting"})
        return True

    def action_done(self):
        for record in self:
            if not record.resolution:
                raise UserError("完成報修前請填寫處理結果。")
            record.write(
                {"state": "done", "completed_date": fields.Datetime.now()}
            )
        return True

    def action_cancel(self):
        self.write({"state": "cancelled"})
        return True

    def action_reopen(self):
        self.write({"state": "new", "completed_date": False})
        return True

