from dateutil.relativedelta import relativedelta

from odoo import api, fields, models


class GgandyAccountingOverview(models.Model):
    _name = "ggandy.accounting.overview"
    _description = "GGAndy 帳務總表"
    _order = "period_start desc, property_id"

    name = fields.Char(string="名稱", compute="_compute_name", store=True)
    period_start = fields.Date(string="月份開始", required=True, index=True)
    period_end = fields.Date(string="月份結束", required=True, index=True)
    property_id = fields.Many2one(
        "ggandy.property",
        string="物件",
        required=True,
        ondelete="cascade",
        index=True,
    )
    owner_id = fields.Many2one(
        "res.partner",
        related="property_id.owner_id",
        string="房東",
        store=True,
        readonly=True,
    )
    active_owner_contract_id = fields.Many2one(
        "ggandy.owner.contract",
        string="當期房東合約",
        compute="_compute_amounts",
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

    rent_receivable = fields.Monetary(string="租金應收", compute="_compute_amounts")
    rent_collected = fields.Monetary(string="租金實收", compute="_compute_amounts")
    tenant_deposit = fields.Monetary(string="租金押金", compute="_compute_amounts")
    owner_payable = fields.Monetary(string="房東應付", compute="_compute_amounts")
    owner_paid = fields.Monetary(string="房東已付", compute="_compute_amounts")
    purchase_cost = fields.Monetary(string="採購費用", compute="_compute_amounts")
    employee_advance = fields.Monetary(string="員工代墊", compute="_compute_amounts")
    net_cash_flow = fields.Monetary(string="現金流小計", compute="_compute_amounts")
    note = fields.Text(string="備註")

    _unique_property_period = models.Constraint(
        "unique(property_id, period_start)",
        "同一物件同一月份只能有一筆 GGAndy 帳務總表。",
    )

    @api.depends("property_id", "period_start")
    def _compute_name(self):
        for record in self:
            period = record.period_start.strftime("%Y-%m") if record.period_start else ""
            record.name = f"{period} / {record.property_id.display_name or ''}".strip(" / ")

    @api.depends("property_id", "period_start", "period_end")
    def _compute_amounts(self):
        Schedule = self.env["ggandy.rent.schedule"]
        Lease = self.env["ggandy.lease"]
        OwnerContract = self.env["ggandy.owner.contract"]
        Move = self.env["account.move"]

        for record in self:
            record.active_owner_contract_id = False
            record.rent_receivable = 0.0
            record.rent_collected = 0.0
            record.tenant_deposit = 0.0
            record.owner_payable = 0.0
            record.owner_paid = 0.0
            record.purchase_cost = 0.0
            record.employee_advance = 0.0
            record.net_cash_flow = 0.0

            if not record.property_id or not record.period_start or not record.period_end:
                continue

            period_domain = [
                ("period_start", ">=", record.period_start),
                ("period_start", "<=", record.period_end),
            ]
            schedules = Schedule.search(
                [("property_id", "=", record.property_id.id)] + period_domain
            )
            record.rent_receivable = sum(schedules.mapped("total_amount"))
            record.rent_collected = sum(
                self._paid_amount(schedule.invoice_id) for schedule in schedules.filtered("invoice_id")
            )

            leases = Lease.search(
                [
                    ("property_id", "=", record.property_id.id),
                    ("start_date", ">=", record.period_start),
                    ("start_date", "<=", record.period_end),
                    ("state", "in", ("active", "expired", "terminated")),
                ]
            )
            record.tenant_deposit = sum(leases.mapped("deposit_amount"))

            contract = OwnerContract.search(
                [
                    ("property_id", "=", record.property_id.id),
                    ("state", "=", "active"),
                    ("start_date", "<=", record.period_end),
                    ("end_date", ">=", record.period_start),
                ],
                limit=1,
                order="start_date desc, id desc",
            )
            record.active_owner_contract_id = contract

            owner_bills = Move
            if contract:
                owner_bills = Move.search(
                    [
                        ("ggandy_owner_contract_id", "=", contract.id),
                        ("ggandy_settlement_period_start", "=", record.period_start),
                        ("move_type", "in", ("in_invoice", "in_refund")),
                        ("state", "!=", "cancel"),
                    ]
                )
            record.owner_payable = sum(self._signed_total(move) for move in owner_bills)
            record.owner_paid = sum(self._paid_amount(move) for move in owner_bills)

            expense_domain = [
                ("ggandy_expense_property_id", "=", record.property_id.id),
                ("invoice_date", ">=", record.period_start),
                ("invoice_date", "<=", record.period_end),
                ("move_type", "in", ("in_invoice", "in_refund")),
                ("state", "!=", "cancel"),
            ]
            purchase_moves = Move.search(expense_domain + [("ggandy_expense_kind", "=", "purchase")])
            advance_moves = Move.search(
                expense_domain + [("ggandy_expense_kind", "=", "employee_advance")]
            )
            record.purchase_cost = sum(self._signed_total(move) for move in purchase_moves)
            record.employee_advance = sum(self._signed_total(move) for move in advance_moves)
            record.net_cash_flow = (
                record.rent_collected
                + record.tenant_deposit
                - record.owner_paid
                - record.purchase_cost
                - record.employee_advance
            )

    @api.model
    def _signed_total(self, move):
        sign = -1 if move.move_type in ("out_refund", "in_refund") else 1
        return sign * move.amount_total

    @api.model
    def _paid_amount(self, move):
        if not move or move.state != "posted":
            return 0.0
        sign = -1 if move.move_type in ("out_refund", "in_refund") else 1
        return sign * max(move.amount_total - move.amount_residual, 0.0)

    @api.model
    def _month_bounds(self, date_value):
        period_start = date_value.replace(day=1)
        period_end = period_start + relativedelta(months=1, days=-1)
        return period_start, period_end

    @api.model
    def ensure_period_records(self, date_value=None):
        date_value = date_value or fields.Date.context_today(self)
        period_start, period_end = self._month_bounds(date_value)
        properties = self.env["ggandy.property"].search([("active", "=", True)])
        existing_property_ids = set(
            self.search(
                [
                    ("property_id", "in", properties.ids),
                    ("period_start", "=", period_start),
                ]
            ).mapped("property_id").ids
        )
        to_create = []
        for property_record in properties:
            if property_record.id not in existing_property_ids:
                to_create.append(
                    {
                        "property_id": property_record.id,
                        "period_start": period_start,
                        "period_end": period_end,
                    }
                )
        if to_create:
            self.create(to_create)
        return True

    @api.model
    def _cron_ensure_current_period_records(self):
        self.ensure_period_records()

    def action_refresh_current_month(self):
        self.env["ggandy.accounting.overview"].ensure_period_records()
        return {
            "type": "ir.actions.client",
            "tag": "reload",
        }
