import calendar
import logging

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError


_logger = logging.getLogger(__name__)


class GgandyOwnerContract(models.Model):
    _name = "ggandy.owner.contract"
    _description = "房東合約"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "start_date desc, name desc"

    name = fields.Char(
        string="合約編號",
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
        required=True,
        default="draft",
        copy=False,
        tracking=True,
    )
    contract_type = fields.Selection(
        [
            ("master_lease", "包租合約"),
            ("agency", "代管合約"),
        ],
        string="合約類型",
        required=True,
        default="agency",
        tracking=True,
    )
    property_id = fields.Many2one(
        "ggandy.property",
        string="管理物件",
        required=True,
        ondelete="restrict",
        tracking=True,
        index=True,
        check_company=True,
    )
    owner_id = fields.Many2one(
        "res.partner",
        string="簽約房東",
        required=True,
        ondelete="restrict",
        tracking=True,
    )
    co_owner_ids = fields.Many2many(
        related="property_id.co_owner_ids",
        string="共同屋主",
        readonly=True,
    )
    start_date = fields.Date(string="合約開始", required=True, tracking=True)
    end_date = fields.Date(string="合約結束", required=True, tracking=True)
    owner_payment_day = fields.Integer(
        string="每月房東付款／結算日",
        default=10,
        required=True,
        help="設定為 1 至 31；實際產生結算資料時，短月將使用當月最後一天。",
    )

    guaranteed_rent = fields.Monetary(
        string="每月保底租金",
        tracking=True,
        help="包租模式下，公司每月應付房東的固定租金。",
    )
    owner_deposit = fields.Monetary(
        string="公司支付房東押金",
        tracking=True,
    )
    fee_type = fields.Selection(
        [
            ("percentage", "按實收租金百分比"),
            ("fixed", "每月固定金額"),
        ],
        string="代管費計算方式",
        default="percentage",
        required=True,
        tracking=True,
    )
    fee_rate = fields.Float(string="代管費率 (%)", digits=(5, 2), tracking=True)
    fixed_fee = fields.Monetary(string="每月固定代管費", tracking=True)

    company_id = fields.Many2one(
        "res.company",
        string="管理公司",
        required=True,
        default=lambda self: self.env.company,
        tracking=True,
        index=True,
    )
    currency_id = fields.Many2one(
        "res.currency",
        related="company_id.currency_id",
        store=True,
        readonly=True,
    )
    manager_id = fields.Many2one(
        "res.users",
        string="合約負責人",
        default=lambda self: self.env.user,
        tracking=True,
        ondelete="set null",
    )
    note = fields.Html(string="合約條款與備註")
    vendor_bill_ids = fields.One2many(
        "account.move",
        "ggandy_owner_contract_id",
        string="房東 Vendor Bills",
    )
    vendor_bill_count = fields.Integer(compute="_compute_vendor_bill_count")

    @api.model_create_multi
    def create(self, vals_list):
        for values in vals_list:
            if values.get("name", "New") == "New":
                values["name"] = self.env["ir.sequence"].next_by_code(
                    "ggandy.owner.contract"
                ) or "New"
        records = super().create(vals_list)
        records.mapped("owner_id").write({"is_ggandy_owner": True})
        return records

    def write(self, values):
        result = super().write(values)
        if "owner_id" in values:
            self.mapped("owner_id").write({"is_ggandy_owner": True})
        return result

    @api.onchange("property_id")
    def _onchange_property_id(self):
        if self.property_id:
            self.owner_id = self.property_id.owner_id
            self.company_id = self.property_id.company_id
            if self.property_id.management_mode in ("master_lease", "agency"):
                self.contract_type = self.property_id.management_mode

    @api.constrains("start_date", "end_date")
    def _check_dates(self):
        for record in self:
            if record.start_date and record.end_date and record.end_date < record.start_date:
                raise ValidationError("房東合約結束日不可早於開始日。")

    @api.constrains("owner_payment_day")
    def _check_owner_payment_day(self):
        for record in self:
            if not 1 <= record.owner_payment_day <= 31:
                raise ValidationError("每月房東付款／結算日必須介於 1 到 31。")

    @api.constrains("guaranteed_rent", "owner_deposit", "fee_rate", "fixed_fee")
    def _check_amounts(self):
        for record in self:
            if record.guaranteed_rent < 0 or record.owner_deposit < 0 or record.fixed_fee < 0:
                raise ValidationError("租金、押金與固定代管費不可小於零。")
            if not 0 <= record.fee_rate <= 100:
                raise ValidationError("代管費率必須介於 0% 到 100%。")

    @api.constrains("property_id", "start_date", "end_date", "state")
    def _check_active_contract_overlap(self):
        for record in self.filtered(lambda item: item.state == "active"):
            overlapping = self.search_count(
                [
                    ("id", "!=", record.id),
                    ("property_id", "=", record.property_id.id),
                    ("state", "=", "active"),
                    ("start_date", "<=", record.end_date),
                    ("end_date", ">=", record.start_date),
                ]
            )
            if overlapping:
                raise ValidationError("此物件在相同期間已有生效中的房東合約。")

    def action_activate(self):
        for record in self:
            if record.state != "draft":
                raise UserError("只有草稿房東合約可以生效。")
            if record.contract_type == "master_lease" and record.guaranteed_rent <= 0:
                raise UserError("包租合約生效前，請填寫每月保底租金。")
            if record.contract_type == "agency":
                if record.fee_type == "percentage" and record.fee_rate <= 0:
                    raise UserError("代管合約生效前，請填寫代管費率。")
                if record.fee_type == "fixed" and record.fixed_fee <= 0:
                    raise UserError("代管合約生效前，請填寫每月固定代管費。")

            record.write({"state": "active"})
            record.property_id.write(
                {
                    "management_mode": record.contract_type,
                    "owner_id": record.owner_id.id,
                    "acquisition_date": record.start_date,
                    "management_end_date": record.end_date,
                }
            )
        return True

    @api.depends("vendor_bill_ids")
    def _compute_vendor_bill_count(self):
        for record in self:
            record.vendor_bill_count = len(
                record.vendor_bill_ids.filtered(lambda move: move.move_type == "in_invoice")
            )

    def action_set_draft(self):
        self.filtered(lambda record: record.state in ("terminated", "cancelled")).write(
            {"state": "draft"}
        )
        return True

    def action_mark_expired(self):
        self.filtered(lambda record: record.state == "active").write({"state": "expired"})
        return True

    def action_terminate(self):
        self.filtered(lambda record: record.state == "active").write(
            {"state": "terminated"}
        )
        return True

    def action_cancel(self):
        self.filtered(lambda record: record.state == "draft").write({"state": "cancelled"})
        return True

    def action_view_vendor_bills(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "房東 Vendor Bills",
            "res_model": "account.move",
            "view_mode": "list,form",
            "domain": [
                ("ggandy_owner_contract_id", "=", self.id),
                ("move_type", "=", "in_invoice"),
            ],
            "context": {
                "default_move_type": "in_invoice",
                "default_ggandy_owner_contract_id": self.id,
                "default_partner_id": self.owner_id.id,
            },
        }

    @api.model
    def _cron_create_owner_vendor_bills(self):
        today = fields.Date.context_today(self)
        contracts = self.search(
            [
                ("state", "=", "active"),
                ("start_date", "<=", today),
                ("end_date", ">=", today.replace(day=1)),
            ]
        )
        for contract in contracts:
            try:
                contract._create_owner_vendor_bill_if_due(today)
            except Exception:
                _logger.exception("Unable to create owner vendor bill for %s.", contract.display_name)

    def _create_owner_vendor_bill_if_due(self, today):
        self.ensure_one()
        period_start = today.replace(day=1)
        period_end = period_start + relativedelta(months=1, days=-1)
        settlement_day = min(
            self.owner_payment_day,
            calendar.monthrange(today.year, today.month)[1],
        )
        if today < today.replace(day=settlement_day):
            return False
        if self.end_date < period_start or self.start_date > period_end:
            return False
        return self._create_owner_vendor_bill(period_start, period_end)

    def _create_owner_vendor_bill(self, period_start, period_end):
        self.ensure_one()
        existing = self.env["account.move"].search(
            [
                ("ggandy_owner_contract_id", "=", self.id),
                ("ggandy_settlement_period_start", "=", period_start),
                ("move_type", "=", "in_invoice"),
                ("state", "!=", "cancel"),
            ],
            limit=1,
        )
        if existing:
            return existing

        amount, label = self._get_owner_settlement_amount(period_start, period_end)
        if amount <= 0:
            return self.env["account.move"]

        purchase_journal = self.env["account.journal"].search(
            [
                ("type", "=", "purchase"),
                ("company_id", "=", self.company_id.id),
            ],
            limit=1,
        )
        if not purchase_journal:
            raise UserError("目前公司尚未設定採購日記帳，無法建立房東 vendor bill。")

        product = self.env.ref(
            "ggandy_property_management.product_product_owner_settlement",
            raise_if_not_found=False,
        )
        bill = self.env["account.move"].create(
            {
                "move_type": "in_invoice",
                "journal_id": purchase_journal.id,
                "partner_id": self.owner_id.id,
                "invoice_date": fields.Date.context_today(self),
                "invoice_date_due": fields.Date.context_today(self),
                "invoice_origin": self.name,
                "ref": f"{self.name} / {period_start.strftime('%Y-%m')}",
                "ggandy_owner_contract_id": self.id,
                "ggandy_settlement_period_start": period_start,
                "ggandy_settlement_period_end": period_end,
                "invoice_line_ids": [
                    fields.Command.create(
                        {
                            "product_id": product.id if product else False,
                            "name": label,
                            "quantity": 1,
                            "price_unit": amount,
                        }
                    )
                ],
            }
        )
        self.message_post(body=f"已建立房東 Vendor Bill {bill.display_name}。")
        return bill

    def _get_owner_settlement_amount(self, period_start, period_end):
        self.ensure_one()
        period_label = period_start.strftime("%Y-%m")
        if self.contract_type == "master_lease":
            return self.guaranteed_rent, f"{self.name} {period_label} 包租保底租金"

        rent_collected = self._get_agency_collected_rent(period_start, period_end)
        if self.fee_type == "percentage":
            management_fee = rent_collected * self.fee_rate / 100.0
        else:
            management_fee = self.fixed_fee if rent_collected else 0.0
        amount = max(rent_collected - management_fee, 0.0)
        return amount, f"{self.name} {period_label} 代管房東結算"

    def _get_agency_collected_rent(self, period_start, period_end):
        self.ensure_one()
        schedules = self.env["ggandy.rent.schedule"].search(
            [
                ("property_id", "=", self.property_id.id),
                ("period_start", ">=", period_start),
                ("period_start", "<=", period_end),
                ("invoice_id.state", "=", "posted"),
            ]
        )
        collected = 0.0
        for schedule in schedules:
            invoice = schedule.invoice_id
            if not invoice.amount_total:
                continue
            paid_ratio = max(invoice.amount_total - invoice.amount_residual, 0.0) / invoice.amount_total
            collected += schedule.rent_amount * paid_ratio
        return collected
