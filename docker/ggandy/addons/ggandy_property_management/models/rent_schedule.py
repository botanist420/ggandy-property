import logging

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError


_logger = logging.getLogger(__name__)


class GgandyRentSchedule(models.Model):
    _name = "ggandy.rent.schedule"
    _description = "租金期次"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "due_date desc, id desc"

    name = fields.Char(string="期次名稱", compute="_compute_name", store=True)
    lease_id = fields.Many2one(
        "ggandy.lease",
        string="租約",
        required=True,
        ondelete="cascade",
        index=True,
        help="這一期租金屬於哪份租約。刪掉租約時，相關期次也會一起移除。",
    )
    property_id = fields.Many2one(
        "ggandy.property",
        related="lease_id.property_id",
        store=True,
        readonly=True,
    )
    unit_id = fields.Many2one(
        "ggandy.property.unit",
        related="lease_id.unit_id",
        store=True,
        readonly=True,
    )
    tenant_id = fields.Many2one(
        "res.partner",
        related="lease_id.tenant_id",
        store=True,
        readonly=True,
    )
    company_id = fields.Many2one(
        "res.company",
        related="lease_id.company_id",
        store=True,
        readonly=True,
        index=True,
    )
    currency_id = fields.Many2one(
        "res.currency",
        related="lease_id.currency_id",
        store=True,
        readonly=True,
    )
    period_start = fields.Date(
        string="計費開始",
        required=True,
        tracking=True,
        help="這一期租金涵蓋的第一天。第一期遇到月中入住時，會從租約開始日算起。",
    )
    period_end = fields.Date(
        string="計費結束",
        required=True,
        tracking=True,
        help="這一期租金涵蓋的最後一天。最後一期遇到月中退租時，會停在租約結束日。",
    )
    due_date = fields.Date(
        string="繳款期限",
        required=True,
        tracking=True,
        help="房客最晚應繳款日期。逾期判斷會看這個日期；期限已過且未收齊時，狀態會轉為已逾期。",
    )
    rent_amount = fields.Monetary(
        string="租金",
        required=True,
        tracking=True,
        help="這一期的租金本金。預設從租約每月租金帶入，但可針對單一期次調整，像臨時折讓或補收就不用改整份租約。",
    )
    management_fee = fields.Monetary(
        string="管理費",
        tracking=True,
        help="這一期另外收的管理費。會和租金一起組成應收合計，對帳時也會一起納入。",
    )
    total_amount = fields.Monetary(
        string="應收合計",
        compute="_compute_total_amount",
        store=True,
        help="公式：租金 + 管理費。這是建立租金帳單前的應收金額，簡單但很關鍵。",
    )
    invoice_id = fields.Many2one(
        "account.move",
        string="客戶發票",
        copy=False,
        readonly=True,
        ondelete="set null",
        help="由這一期建立出的客戶發票。已有發票時，再按建立帳單會直接開啟原帳單，不會重複建立。",
    )
    invoice_state = fields.Selection(
        related="invoice_id.state",
        string="發票狀態",
        help="顯示連結帳單的會計狀態，例如草稿、已過帳或取消。要不要算入實收，主要看它有沒有過帳。",
    )
    payment_state = fields.Selection(
        related="invoice_id.payment_state",
        string="付款狀態",
        help="顯示連結帳單的付款狀態。部分收款也會被辨識，不只區分已付或未付。",
    )
    collection_state = fields.Selection(
        [
            ("uninvoiced", "尚未開單"),
            ("draft", "草稿帳單"),
            ("unpaid", "待收款"),
            ("partial", "部分收款"),
            ("paid", "已收款"),
            ("overdue", "已逾期"),
            ("cancelled", "已取消"),
        ],
        string="收租狀態",
        compute="_compute_collection_state",
        help="系統依帳單與付款狀態判斷：未開單、草稿、待收款、部分收款、已收款、已逾期或已取消。沒有發票先算未開單；過期又沒收齊，就會被標成已逾期。",
    )
    note = fields.Text(string="備註")

    @api.model
    def _get_unpaid_domain(self):
        return [
            ("invoice_id", "!=", False),
            ("invoice_id.state", "!=", "cancel"),
            "|",
            ("invoice_id.state", "=", "draft"),
            "&",
            ("invoice_id.state", "=", "posted"),
            ("invoice_id.payment_state", "not in", ("paid", "reversed")),
        ]

    @api.model
    def _get_overdue_domain(self):
        today = fields.Date.context_today(self)
        return [
            ("due_date", "<", today),
            ("lease_id.state", "=", "active"),
            "|",
            ("invoice_id", "=", False),
            "&",
            ("invoice_id.state", "!=", "cancel"),
            "|",
            ("invoice_id.state", "=", "draft"),
            "&",
            ("invoice_id.state", "=", "posted"),
            ("invoice_id.payment_state", "not in", ("paid", "reversed")),
        ]

    @api.depends("lease_id.name", "period_start")
    def _compute_name(self):
        for record in self:
            period = record.period_start.strftime("%Y-%m") if record.period_start else ""
            record.name = f"{record.lease_id.name or ''} / {period}".strip(" / ")

    @api.depends("rent_amount", "management_fee")
    def _compute_total_amount(self):
        for record in self:
            record.total_amount = record.rent_amount + record.management_fee

    @api.depends("invoice_id", "invoice_id.state", "invoice_id.payment_state", "due_date")
    def _compute_collection_state(self):
        today = fields.Date.context_today(self)
        for record in self:
            if not record.invoice_id:
                record.collection_state = "uninvoiced"
            elif record.invoice_id.state == "cancel":
                record.collection_state = "cancelled"
            elif record.invoice_id.state == "draft":
                record.collection_state = "draft"
            elif record.invoice_id.payment_state == "paid":
                record.collection_state = "paid"
            elif record.invoice_id.payment_state == "partial":
                record.collection_state = "partial"
            elif record.due_date and record.due_date < today:
                record.collection_state = "overdue"
            else:
                record.collection_state = "unpaid"

    @api.constrains("period_start", "period_end", "due_date")
    def _check_dates(self):
        for record in self:
            if record.period_start and record.period_end and record.period_end < record.period_start:
                raise ValidationError("計費結束日不可早於開始日。")

    @api.constrains("rent_amount", "management_fee")
    def _check_amounts(self):
        for record in self:
            if record.rent_amount < 0 or record.management_fee < 0:
                raise ValidationError("租金與管理費不可小於零。")

    def action_create_invoice(self):
        self.ensure_one()
        if self.invoice_id:
            return self.action_open_invoice()
        if self.total_amount <= 0:
            raise UserError("應收合計必須大於零。")
        if not self.tenant_id:
            raise UserError("租約必須設定主承租人。")

        sale_journal = self.env["account.journal"].search(
            [
                ("type", "=", "sale"),
                ("company_id", "=", self.company_id.id),
            ],
            limit=1,
        )
        if not sale_journal:
            raise UserError(
                "目前公司尚未設定銷售日記帳。請先到會計設定完成台灣會計本地化，"
                "確認公司國家、會計科目與銷售日記帳後再建立租金帳單。"
            )

        rent_product = self.env.ref(
            "ggandy_property_management.product_product_rent", raise_if_not_found=False
        )
        fee_product = self.env.ref(
            "ggandy_property_management.product_product_management_fee", raise_if_not_found=False
        )
        invoice_lines = []
        if self.rent_amount:
            invoice_lines.append(
                fields.Command.create(
                    {
                        "product_id": rent_product.id if rent_product else False,
                        "name": f"{self.name} 租金",
                        "quantity": 1,
                        "price_unit": self.rent_amount,
                    }
                )
            )
        if self.management_fee:
            invoice_lines.append(
                fields.Command.create(
                    {
                        "product_id": fee_product.id if fee_product else False,
                        "name": f"{self.name} 管理費",
                        "quantity": 1,
                        "price_unit": self.management_fee,
                    }
                )
            )

        invoice = self.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "journal_id": sale_journal.id,
                "partner_id": self.tenant_id.id,
                "invoice_date": fields.Date.context_today(self),
                "invoice_date_due": self.due_date,
                "invoice_origin": self.lease_id.name,
                "ref": self.name,
                "ggandy_lease_id": self.lease_id.id,
                "invoice_line_ids": invoice_lines,
            }
        )
        self.invoice_id = invoice
        self.message_post(body=f"已建立租金帳單 {invoice.display_name}。")
        return self.action_open_invoice()

    @api.model
    def _cron_create_due_invoices(self):
        today = fields.Date.context_today(self)
        schedules = self.search(
            [
                ("invoice_id", "=", False),
                ("period_start", "<=", today),
                ("lease_id.state", "=", "active"),
            ]
        )
        for schedule in schedules:
            try:
                schedule.action_create_invoice()
            except Exception:
                _logger.exception("Unable to create rent invoice for %s.", schedule.display_name)

    @api.model
    def _cron_create_overdue_rent_activities(self):
        schedules = self.search(self._get_overdue_domain())
        activity_type = self.env.ref("mail.mail_activity_data_todo", raise_if_not_found=False)
        model_id = self.env["ir.model"]._get_id(self._name)
        if not activity_type or not model_id:
            return

        Activity = self.env["mail.activity"].sudo()
        for schedule in schedules:
            user = schedule.property_id.manager_id or schedule.lease_id.create_uid or self.env.user
            existing = Activity.search_count(
                [
                    ("res_model_id", "=", model_id),
                    ("res_id", "=", schedule.id),
                    ("activity_type_id", "=", activity_type.id),
                    ("user_id", "=", user.id),
                    ("summary", "=", "逾期租金待處理"),
                ]
            )
            if existing:
                continue

            currency = schedule.currency_id.symbol or schedule.currency_id.name or ""
            Activity.create(
                {
                    "activity_type_id": activity_type.id,
                    "summary": "逾期租金待處理",
                    "note": (
                        f"{schedule.display_name} 已逾期，"
                        f"應收合計 {currency}{schedule.total_amount:,.0f}。"
                    ),
                    "date_deadline": fields.Date.context_today(schedule),
                    "res_model_id": model_id,
                    "res_id": schedule.id,
                    "user_id": user.id,
                }
            )

    def action_open_invoice(self):
        self.ensure_one()
        if not self.invoice_id:
            raise UserError("尚未建立客戶發票。")
        return {
            "type": "ir.actions.act_window",
            "name": "租金帳單",
            "res_model": "account.move",
            "res_id": self.invoice_id.id,
            "view_mode": "form",
        }
