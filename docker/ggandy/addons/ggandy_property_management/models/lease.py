import calendar

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError


class GgandyLease(models.Model):
    _name = "ggandy.lease"
    _description = "租約"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "start_date desc, name desc"

    name = fields.Char(
        string="租約編號",
        required=True,
        copy=False,
        readonly=True,
        default="New",
        index=True,
    )
    active = fields.Boolean(default=True)
    state = fields.Selection(
        [
            ("draft", "草稿"),
            ("active", "生效中"),
            ("expired", "已到期"),
            ("terminated", "提前終止"),
            ("cancelled", "已取消"),
        ],
        string="狀態",
        default="draft",
        required=True,
        copy=False,
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
        required=True,
        ondelete="restrict",
        tracking=True,
        index=True,
        domain="[('property_id', '=', property_id), ('active', '=', True)]",
    )
    tenant_id = fields.Many2one(
        "res.partner",
        string="主承租人／帳單對象",
        required=True,
        ondelete="restrict",
        tracking=True,
    )
    co_tenant_ids = fields.Many2many(
        "res.partner",
        "ggandy_lease_co_tenant_rel",
        "lease_id",
        "partner_id",
        string="共同承租人／居住人",
    )
    start_date = fields.Date(string="租期開始", required=True, tracking=True)
    end_date = fields.Date(string="租期結束", required=True, tracking=True)
    rent_amount = fields.Monetary(string="每月租金", required=True, tracking=True)
    deposit_amount = fields.Monetary(string="押金", tracking=True)
    management_fee = fields.Monetary(string="每月管理費", tracking=True)
    rent_due_day = fields.Integer(
        string="每月繳租日",
        required=True,
        default=5,
        help="設定為 1 至 31；若該月沒有此日期，系統會使用當月最後一天。",
    )
    auto_generate_schedule = fields.Boolean(
        string="生效時建立租金期次",
        default=True,
    )
    company_id = fields.Many2one(
        "res.company",
        string="公司",
        required=True,
        default=lambda self: self.env.company,
        index=True,
    )
    currency_id = fields.Many2one(
        "res.currency",
        related="company_id.currency_id",
        store=True,
        readonly=True,
    )
    schedule_ids = fields.One2many(
        "ggandy.rent.schedule", "lease_id", string="租金期次"
    )
    invoice_ids = fields.One2many("account.move", "ggandy_lease_id", string="租金帳單")
    maintenance_request_ids = fields.One2many(
        "ggandy.maintenance.request", "lease_id", string="報修單"
    )
    schedule_count = fields.Integer(compute="_compute_counts")
    invoice_count = fields.Integer(compute="_compute_counts")
    maintenance_count = fields.Integer(compute="_compute_counts")
    note = fields.Html(string="合約備註")

    @api.model_create_multi
    def create(self, vals_list):
        for values in vals_list:
            if values.get("name", "New") == "New":
                values["name"] = self.env["ir.sequence"].next_by_code(
                    "ggandy.lease"
                ) or "New"
        records = super().create(vals_list)
        records.mapped("tenant_id").write({"is_ggandy_tenant": True})
        records.mapped("co_tenant_ids").write({"is_ggandy_tenant": True})
        return records

    def write(self, values):
        result = super().write(values)
        if "tenant_id" in values or "co_tenant_ids" in values:
            self.mapped("tenant_id").write({"is_ggandy_tenant": True})
            self.mapped("co_tenant_ids").write({"is_ggandy_tenant": True})
        return result

    @api.onchange("property_id")
    def _onchange_property_id(self):
        if self.unit_id and self.unit_id.property_id != self.property_id:
            self.unit_id = False

    @api.onchange("unit_id")
    def _onchange_unit_id(self):
        if not self.unit_id:
            return
        self.property_id = self.unit_id.property_id
        if not self.rent_amount:
            self.rent_amount = self.unit_id.monthly_rent
        if not self.deposit_amount:
            self.deposit_amount = self.unit_id.monthly_rent * self.unit_id.deposit_months

    @api.depends("schedule_ids", "invoice_ids", "maintenance_request_ids")
    def _compute_counts(self):
        for record in self:
            record.schedule_count = len(record.schedule_ids)
            record.invoice_count = len(record.invoice_ids.filtered(lambda move: move.move_type == "out_invoice"))
            record.maintenance_count = len(record.maintenance_request_ids)

    @api.constrains("start_date", "end_date")
    def _check_dates(self):
        for record in self:
            if record.start_date and record.end_date and record.end_date < record.start_date:
                raise ValidationError("租期結束日不可早於開始日。")

    @api.constrains("rent_due_day")
    def _check_rent_due_day(self):
        for record in self:
            if not 1 <= record.rent_due_day <= 31:
                raise ValidationError("每月繳租日必須介於 1 到 31。")

    @api.constrains("rent_amount", "deposit_amount", "management_fee")
    def _check_amounts(self):
        for record in self:
            if record.rent_amount <= 0:
                raise ValidationError("每月租金必須大於零。")
            if record.deposit_amount < 0 or record.management_fee < 0:
                raise ValidationError("押金與管理費不可小於零。")

    @api.constrains("unit_id", "start_date", "end_date", "state")
    def _check_active_lease_overlap(self):
        for record in self.filtered(lambda item: item.state == "active"):
            overlapping = self.search_count(
                [
                    ("id", "!=", record.id),
                    ("unit_id", "=", record.unit_id.id),
                    ("state", "=", "active"),
                    ("start_date", "<=", record.end_date),
                    ("end_date", ">=", record.start_date),
                ]
            )
            if overlapping:
                raise ValidationError("此出租單位在相同期間已有生效中的租約。")

    def action_activate(self):
        for record in self:
            if record.state != "draft":
                raise UserError("只有草稿租約可以生效。")
            record.write({"state": "active"})
            record.unit_id.state = "occupied"
            if record.auto_generate_schedule:
                record._generate_rent_schedule()
        self.env["ggandy.rent.schedule"]._cron_create_due_invoices()
        return True

    def action_set_draft(self):
        for record in self:
            if record.invoice_ids.filtered(lambda move: move.state == "posted"):
                raise UserError("已有過帳帳單的租約不可退回草稿。")
            record.state = "draft"
            record._refresh_unit_state()
        return True

    def action_terminate(self):
        self.write({"state": "terminated"})
        self.mapped("unit_id")._refresh_from_active_leases()
        return True

    def action_cancel(self):
        for record in self:
            if record.invoice_ids.filtered(lambda move: move.state == "posted"):
                raise UserError("已有過帳帳單的租約不可取消。")
            record.state = "cancelled"
            record._refresh_unit_state()
        return True

    def _refresh_unit_state(self):
        self.mapped("unit_id")._refresh_from_active_leases()

    def action_generate_schedule(self):
        self.ensure_one()
        self._generate_rent_schedule()
        return self.action_view_schedules()

    def _generate_rent_schedule(self):
        Schedule = self.env["ggandy.rent.schedule"]
        for lease in self:
            if not lease.start_date or not lease.end_date:
                raise UserError("請先填寫完整租期。")
            month_cursor = lease.start_date.replace(day=1)
            final_month = lease.end_date.replace(day=1)
            while month_cursor <= final_month:
                period_start = max(lease.start_date, month_cursor)
                month_end = month_cursor + relativedelta(months=1, days=-1)
                period_end = min(lease.end_date, month_end)
                due_day = min(
                    lease.rent_due_day,
                    calendar.monthrange(month_cursor.year, month_cursor.month)[1],
                )
                due_date = month_cursor.replace(day=due_day)
                if due_date < lease.start_date:
                    due_date = lease.start_date
                existing = Schedule.search_count(
                    [
                        ("lease_id", "=", lease.id),
                        ("period_start", "=", period_start),
                    ]
                )
                if not existing:
                    Schedule.create(
                        {
                            "lease_id": lease.id,
                            "period_start": period_start,
                            "period_end": period_end,
                            "due_date": due_date,
                            "rent_amount": lease.rent_amount,
                            "management_fee": lease.management_fee,
                        }
                    )
                month_cursor += relativedelta(months=1)

    def action_view_schedules(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "租金期次",
            "res_model": "ggandy.rent.schedule",
            "view_mode": "list,form",
            "domain": [("lease_id", "=", self.id)],
            "context": {"default_lease_id": self.id},
        }

    def action_view_invoices(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "租金帳單",
            "res_model": "account.move",
            "view_mode": "list,form",
            "domain": [("ggandy_lease_id", "=", self.id), ("move_type", "=", "out_invoice")],
            "context": {"default_move_type": "out_invoice", "default_ggandy_lease_id": self.id},
        }

    def action_view_maintenance(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "報修單",
            "res_model": "ggandy.maintenance.request",
            "view_mode": "list,form",
            "domain": [("lease_id", "=", self.id)],
            "context": {
                "default_lease_id": self.id,
                "default_property_id": self.property_id.id,
                "default_unit_id": self.unit_id.id,
                "default_tenant_id": self.tenant_id.id,
            },
        }
