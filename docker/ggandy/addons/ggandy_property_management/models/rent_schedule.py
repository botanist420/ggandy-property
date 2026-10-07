import calendar
import logging

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import float_round

from .property_unit import ELECTRICITY_BILLING_TYPES, UTILITY_KINDS, WATER_BILLING_TYPES


_logger = logging.getLogger(__name__)

UTILITY_LABELS = {"electricity": "電費", "water": "水費"}
UTILITY_PRODUCT_XMLIDS = {
    "electricity": "ggandy_property_management.product_product_electricity",
    "water": "ggandy_property_management.product_product_water",
}
# 帳單過帳後就不能再改的水電欄位（改了會跟帳單對不起來）。
UTILITY_INPUT_FIELDS = tuple(
    f"{kind}_{suffix}"
    for kind in UTILITY_KINDS
    for suffix in ("billing_type", "rate", "fixed_fee", "prev_reading", "curr_reading")
)


def _format_number(value):
    return f"{value:,.2f}".rstrip("0").rstrip(".")


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
    electricity_billing_type = fields.Selection(
        ELECTRICITY_BILLING_TYPES,
        string="電費計費方式",
        help="建立期次時從出租單位帶入。出租單位改設定後，帳單還沒過帳的期次會跟著更新；只想調整這一期也可以直接改。",
    )
    electricity_rate = fields.Float(
        string="每度電價",
        digits=(10, 2),
        help="這一期的每度電價，預設從出租單位帶入。",
    )
    electricity_fixed_fee = fields.Monetary(
        string="固定電費",
        help="計費方式為「每月固定金額」時這一期收的電費；月中入住或退租的那一期會按日計算。",
    )
    electricity_prev_reading = fields.Float(
        string="電錶上期度數",
        digits=(12, 1),
        tracking=True,
        help="上一次抄表的度數。第一期帶入租約的「入住電錶度數」，之後每期自動帶入前一期的本期度數；換電錶時請手動改成新電錶的起始度數。",
    )
    electricity_curr_reading = fields.Float(
        string="電錶本期度數",
        digits=(12, 1),
        tracking=True,
        help="這次抄表看到的電錶度數。填好後會算出用電度數與電費，並自動帶到下一期的上期度數；帳單還是草稿的話，帳單上的電費也會一起更新。",
    )
    electricity_usage = fields.Float(
        string="用電度數",
        digits=(12, 1),
        compute="_compute_utility_amounts",
        store=True,
        help="公式：電錶本期度數 − 上期度數。還沒填本期度數時為 0。",
    )
    electricity_amount = fields.Monetary(
        string="電費",
        compute="_compute_utility_amounts",
        store=True,
        help="依度數計費＝用電度數 × 每度電價（四捨五入到元）；每月固定金額＝固定電費；含在租金內或房客自繳＝0。會併入應收合計並開在同一張帳單。",
    )
    water_billing_type = fields.Selection(
        WATER_BILLING_TYPES,
        string="水費計費方式",
        help="建立期次時從出租單位帶入。出租單位改設定後，帳單還沒過帳的期次會跟著更新；只想調整這一期也可以直接改。",
    )
    water_rate = fields.Float(
        string="每度水價",
        digits=(10, 2),
        help="這一期的每度水價，預設從出租單位帶入。",
    )
    water_fixed_fee = fields.Monetary(
        string="固定水費",
        help="計費方式為「每月固定金額」時這一期收的水費；月中入住或退租的那一期會按日計算。",
    )
    water_prev_reading = fields.Float(
        string="水錶上期度數",
        digits=(12, 1),
        tracking=True,
        help="上一次抄表的度數。第一期帶入租約的「入住水錶度數」，之後每期自動帶入前一期的本期度數；換水錶時請手動改成新水錶的起始度數。",
    )
    water_curr_reading = fields.Float(
        string="水錶本期度數",
        digits=(12, 1),
        tracking=True,
        help="這次抄表看到的水錶度數。填好後會算出用水度數與水費，並自動帶到下一期的上期度數；帳單還是草稿的話，帳單上的水費也會一起更新。",
    )
    water_usage = fields.Float(
        string="用水度數",
        digits=(12, 1),
        compute="_compute_utility_amounts",
        store=True,
        help="公式：水錶本期度數 − 上期度數。還沒填本期度數時為 0。",
    )
    water_amount = fields.Monetary(
        string="水費",
        compute="_compute_utility_amounts",
        store=True,
        help="依度數計費＝用水度數 × 每度水價（四捨五入到元）；每月固定金額＝固定水費；含在租金內或房客自繳＝0。會併入應收合計並開在同一張帳單。",
    )
    meter_reading_pending = fields.Boolean(
        string="待抄表",
        compute="_compute_utility_amounts",
        store=True,
        help="水電依度數計費、但這一期還沒填本期度數。帳單過帳前記得先抄表，不然這期的水電費不會收到。",
    )
    total_amount = fields.Monetary(
        string="應收合計",
        compute="_compute_total_amount",
        store=True,
        help="公式：租金 + 管理費 + 電費 + 水費。這是建立租金帳單前的應收金額，簡單但很關鍵。",
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

    @api.depends(
        "electricity_billing_type",
        "electricity_rate",
        "electricity_fixed_fee",
        "electricity_prev_reading",
        "electricity_curr_reading",
        "water_billing_type",
        "water_rate",
        "water_fixed_fee",
        "water_prev_reading",
        "water_curr_reading",
    )
    def _compute_utility_amounts(self):
        for record in self:
            pending = False
            for kind in UTILITY_KINDS:
                usage, amount = record._get_utility_charge(kind)
                record[f"{kind}_usage"] = usage
                record[f"{kind}_amount"] = amount
                if record[f"{kind}_billing_type"] == "metered" and not record[f"{kind}_curr_reading"]:
                    pending = True
            record.meter_reading_pending = pending

    def _get_utility_charge(self, kind):
        """回傳 (度數, 金額)。依度數計費 = (本期 − 上期) × 單價，四捨五入到元。"""
        self.ensure_one()
        billing_type = self[f"{kind}_billing_type"]
        if billing_type == "metered":
            current = self[f"{kind}_curr_reading"]
            if not current:
                return 0.0, 0.0
            usage = max(current - self[f"{kind}_prev_reading"], 0.0)
            return usage, float_round(usage * self[f"{kind}_rate"], precision_rounding=1.0)
        if billing_type == "fixed":
            return 0.0, self[f"{kind}_fixed_fee"]
        return 0.0, 0.0

    @api.depends("rent_amount", "management_fee", "electricity_amount", "water_amount")
    def _compute_total_amount(self):
        for record in self:
            record.total_amount = (
                record.rent_amount
                + record.management_fee
                + record.electricity_amount
                + record.water_amount
            )

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

    @api.constrains(
        "electricity_rate",
        "electricity_fixed_fee",
        "electricity_prev_reading",
        "electricity_curr_reading",
        "water_rate",
        "water_fixed_fee",
        "water_prev_reading",
        "water_curr_reading",
    )
    def _check_utility_values(self):
        for record in self:
            for kind in UTILITY_KINDS:
                label = "電" if kind == "electricity" else "水"
                if (
                    record[f"{kind}_rate"] < 0
                    or record[f"{kind}_fixed_fee"] < 0
                    or record[f"{kind}_prev_reading"] < 0
                    or record[f"{kind}_curr_reading"] < 0
                ):
                    raise ValidationError(f"{record.display_name}：{label}費單價、固定金額與{label}錶度數不可小於零。")
                current = record[f"{kind}_curr_reading"]
                if current and current < record[f"{kind}_prev_reading"]:
                    raise ValidationError(
                        f"{record.display_name}：{label}錶本期度數（{_format_number(current)}）"
                        f"不可小於上期度數（{_format_number(record[f'{kind}_prev_reading'])}）。"
                        f"如果換過{label}錶，請把上期度數改成新{label}錶的起始度數。"
                    )

    def write(self, values):
        utility_changed = any(field in values for field in UTILITY_INPUT_FIELDS)
        if utility_changed and self.filtered(lambda record: record.invoice_id.state == "posted"):
            raise UserError(
                "帳單已過帳的期次不能再修改水電度數或費率。"
                "若抄錯表，請在下一期調整，或另外開立補收／折讓帳單。"
            )
        result = super().write(values)
        for kind in UTILITY_KINDS:
            if f"{kind}_curr_reading" in values or f"{kind}_prev_reading" in values:
                self._propagate_meter_reading(kind)
        if utility_changed:
            self._sync_draft_invoice_utility_lines()
        return result

    def _get_period_ratio(self):
        """這一期占當月的比例，月中入住／退租時小於 1。"""
        self.ensure_one()
        days_in_month = calendar.monthrange(self.period_start.year, self.period_start.month)[1]
        return ((self.period_end - self.period_start).days + 1) / days_in_month

    def _propagate_meter_reading(self, kind):
        """把這一期最後的讀數帶到之後期次的上期度數，直到遇到下一筆已抄表的期次。"""
        for record in self.sorted("period_start"):
            reading = record[f"{kind}_curr_reading"] or record[f"{kind}_prev_reading"]
            later = self.search(
                [
                    ("lease_id", "=", record.lease_id.id),
                    ("period_start", ">", record.period_start),
                ],
                order="period_start",
            )
            later._apply_previous_reading(kind, reading)

    def _apply_previous_reading(self, kind, reading):
        """self 需依 period_start 排序；已過帳的期次不動。"""
        prev_field = f"{kind}_prev_reading"
        for record in self:
            if record.invoice_id.state != "posted" and record[prev_field] != reading:
                # 用 super 寫入避免連鎖觸發 _propagate_meter_reading，下面的迴圈會自己往後帶。
                super(GgandyRentSchedule, record).write({prev_field: reading})
                record._sync_draft_invoice_utility_lines()
            if record[f"{kind}_curr_reading"]:
                break

    def _get_utility_product(self, kind):
        return self.env.ref(UTILITY_PRODUCT_XMLIDS[kind], raise_if_not_found=False)

    def _prepare_utility_invoice_line_vals(self, kind):
        """這一期的水電帳單明細；金額為 0 時回傳 None。"""
        self.ensure_one()
        amount = self[f"{kind}_amount"]
        if not amount:
            return None
        label = UTILITY_LABELS[kind]
        if self[f"{kind}_billing_type"] == "metered":
            description = (
                f"{self.name} {label}（{_format_number(self[f'{kind}_prev_reading'])} → "
                f"{_format_number(self[f'{kind}_curr_reading'])}，"
                f"{_format_number(self[f'{kind}_usage'])} 度 × {_format_number(self[f'{kind}_rate'])} 元）"
            )
        else:
            description = f"{self.name} {label}（每月固定）"
        product = self._get_utility_product(kind)
        return {
            "product_id": product.id if product else False,
            "name": description,
            "quantity": 1,
            "price_unit": amount,
        }

    def _sync_draft_invoice_utility_lines(self):
        """帳單還是草稿時，讓帳單上的水電明細跟期次一致。"""
        for record in self.filtered(lambda item: item.invoice_id.state == "draft"):
            commands = []
            for kind in UTILITY_KINDS:
                product = record._get_utility_product(kind)
                lines = (
                    record.invoice_id.invoice_line_ids.filtered(lambda line: line.product_id == product)
                    if product
                    else self.env["account.move.line"]
                )
                vals = record._prepare_utility_invoice_line_vals(kind)
                if vals and lines:
                    commands.append(fields.Command.update(lines[0].id, vals))
                    commands += [fields.Command.unlink(line.id) for line in lines[1:]]
                elif vals:
                    commands.append(fields.Command.create(vals))
                else:
                    commands += [fields.Command.unlink(line.id) for line in lines]
            if commands:
                record.invoice_id.write({"invoice_line_ids": commands})

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
        for kind in UTILITY_KINDS:
            utility_vals = self._prepare_utility_invoice_line_vals(kind)
            if utility_vals:
                invoice_lines.append(fields.Command.create(utility_vals))

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
