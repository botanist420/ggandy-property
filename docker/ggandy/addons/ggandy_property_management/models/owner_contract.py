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
        help="包租代表公司固定付房東保底租金；代管代表按實收租金扣代管費後結算給房東。",
    )
    property_id = fields.Many2one(
        "ggandy.property",
        string="管理物件",
        required=True,
        ondelete="restrict",
        tracking=True,
        index=True,
        check_company=True,
        help="這份房東合約綁定的物件。合約生效後會同步更新物件的經營模式、房東與管理期間。",
    )
    owner_id = fields.Many2one(
        "res.partner",
        string="簽約房東",
        required=True,
        ondelete="restrict",
        tracking=True,
        domain=[("is_ggandy_owner", "=", True)],
        help="房東帳單開給這位房東。只能選標記為「房東」的聯絡人；直接輸入新名字建立的會自動標記。",
    )
    start_date = fields.Date(
        string="合約開始",
        required=True,
        tracking=True,
        help="合約從哪天開始。只有落在合約期間內的月份才會自動開房東帳單。",
    )
    end_date = fields.Date(
        string="合約結束",
        required=True,
        tracking=True,
        help="房東合約最後一天。若結束日早於本月起始，系統不會再建立該合約的結算單。",
    )
    owner_payment_day = fields.Integer(
        string="房東匯款日",
        default=10,
        required=True,
        help="每月幾號匯款給房東（1–31，小月遇到 29–31 號就用月底）。系統會在這天自動開當月房東帳單；補開過去月份時，帳單日期也用這天。",
    )

    guaranteed_rent = fields.Monetary(
        string="每月保底租金",
        tracking=True,
        help="包租模式下，公司每月應付房東的固定租金。即使房客尚未付款，仍會依合約計算房東應付金額。",
    )
    owner_deposit = fields.Monetary(
        string="公司支付房東押金",
        tracking=True,
        help="公司付給房東的押金，用來記錄合約條件。目前不會自動進入每月房東結算。",
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
        help="代管模式要怎麼扣服務費：按實收租金百分比，或每月固定金額。",
    )
    fee_rate = fields.Float(
        string="代管費率 (%)",
        digits=(5, 2),
        default=8.0,
        tracking=True,
        help="代管費採百分比時使用。房東結算會以實收租金乘上此比例扣除；新合約預設 8%；填 8 代表 8%，不是 0.08。",
    )
    fixed_fee = fields.Monetary(
        string="每月固定代管費",
        tracking=True,
        help="代管費採固定金額時使用。當月有實收租金才會扣這筆；完全沒收到租金時不會扣除。",
    )

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
    adjustment_ids = fields.One2many(
        "ggandy.owner.adjustment",
        "contract_id",
        string="帳單調整",
        help="依月份記錄要加進房東帳單的調整，例如押金退還、租金扣回。每筆會在那個月的房東帳單多一行。",
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
        string="房東帳單",
    )
    vendor_bill_count = fields.Integer(
        compute="_compute_vendor_bill_count",
        help="這份合約已開的房東帳單數量（不代表已付款）。",
    )
    has_pending_adjustments = fields.Boolean(
        string="有帳單調整還沒加入帳單",
        compute="_compute_has_pending_adjustments",
        help="有帳單調整的那個月已經開了房東帳單，但這筆調整還沒加進去。",
    )
    has_missing_past_bills = fields.Boolean(
        string="有過去月份沒開房東帳單",
        compute="_compute_has_missing_past_bills",
        help="合約開始到這次房東匯款日之前，有月份還沒開房東帳單（例如合約從過去的月份起算）。",
    )

    @api.model_create_multi
    def create(self, vals_list):
        for values in vals_list:
            if "owner_payment_day" not in values and values.get("property_id"):
                property_day = self.env["ggandy.property"].browse(
                    values["property_id"]
                ).owner_payment_day
                if property_day:
                    values["owner_payment_day"] = property_day
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
            if self.property_id.owner_payment_day:
                self.owner_payment_day = self.property_id.owner_payment_day
            if self.property_id.management_mode in ("master_lease", "agency"):
                self.contract_type = self.property_id.management_mode
            # 合約期間預設帶物件的管理期間；已經填了就不覆蓋
            if not self.start_date and self.property_id.acquisition_date:
                self.start_date = self.property_id.acquisition_date
            if not self.end_date and self.property_id.management_end_date:
                self.end_date = self.property_id.management_end_date

    @api.constrains("start_date", "end_date")
    def _check_dates(self):
        for record in self:
            if record.start_date and record.end_date and record.end_date < record.start_date:
                raise ValidationError("房東合約結束日不可早於開始日。")

    @api.constrains("owner_payment_day")
    def _check_owner_payment_day(self):
        for record in self:
            if not 1 <= record.owner_payment_day <= 31:
                raise ValidationError("房東匯款日必須介於 1 到 31。")

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
        # 合約從過去的月份起算時，生效後直接跳出補開歷史房東帳單
        if len(self) == 1 and self._get_missing_bill_periods():
            return self.action_open_bill_backfill_wizard()
        return True

    @api.depends("vendor_bill_ids")
    def _compute_vendor_bill_count(self):
        for record in self:
            record.vendor_bill_count = len(
                record.vendor_bill_ids.filtered(lambda move: move.move_type == "in_invoice")
            )

    @api.depends("adjustment_ids.is_applied", "adjustment_ids.period_start", "vendor_bill_ids.state")
    def _compute_has_pending_adjustments(self):
        for record in self:
            record.has_pending_adjustments = bool(record._get_pending_adjustments())

    def _get_month_bill(self, period_start):
        """這份合約某個帳單月份的房東帳單（未取消的那張，一個月只會有一張）。"""
        self.ensure_one()
        return self.vendor_bill_ids.filtered(
            lambda move: move.move_type == "in_invoice"
            and move.state != "cancel"
            and move.ggandy_settlement_period_start == period_start
        )[:1]

    def _get_pending_adjustments(self):
        """還沒加入帳單、但那個月已經有房東帳單的調整。還沒開帳單的月份不算，開帳單時會自動帶入。"""
        self.ensure_one()
        return self.adjustment_ids.filtered(
            lambda adjustment: not adjustment.is_applied and self._get_month_bill(adjustment.period_start)
        )

    def action_apply_adjustments_to_bills(self):
        """把還沒加入的帳單調整加進那個月的房東帳單：草稿帳單直接多一行，已過帳的不動、提醒同事。"""
        self.ensure_one()
        applied, skipped = [], []
        for adjustment in self._get_pending_adjustments().sorted(lambda item: (item.period_start, item.id)):
            bill = self._get_month_bill(adjustment.period_start)
            month = adjustment.period_start.strftime("%Y-%m")
            if bill.state != "draft":
                skipped.append(f"{month} {adjustment.name}")
                continue
            adjustment._add_to_bill(bill)
            applied.append(f"{month} {adjustment.name}")
        messages = []
        if applied:
            messages.append(f"已插入房東帳單：{'、'.join(applied)}。")
        if skipped:
            messages.append(f"帳單已過帳、沒有插入：{'、'.join(skipped)}；要加請先把那張帳單重設為草稿。")
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "插入帳單",
                "message": " ".join(messages) or "沒有需要插入的帳單調整。",
                "type": "warning" if skipped else "success",
                "sticky": bool(skipped),
                "next": {"type": "ir.actions.client", "tag": "soft_reload"},
            },
        }

    def _compute_has_missing_past_bills(self):
        for record in self:
            record.has_missing_past_bills = record.state == "active" and bool(record._get_missing_bill_periods())

    def _get_settlement_date(self, month_start):
        """某個月的房東結算日（owner_payment_day，短月取月底）。"""
        self.ensure_one()
        last_day = calendar.monthrange(month_start.year, month_start.month)[1]
        return month_start.replace(day=min(self.owner_payment_day, last_day))

    def _get_missing_bill_periods(self, today=None):
        """合約開始到今天為止、結算日已經過了卻還沒開房東帳單的月份 [(月初, 月底), ...]。

        已有未取消帳單的月份不列；應付 ≤ 0 的月份也不列（_create_owner_vendor_bill 不會開）。
        """
        self.ensure_one()
        if not self.start_date or not self.end_date:
            return []
        today = today or fields.Date.context_today(self)
        billed = set(
            self.env["account.move"]
            .search(
                [
                    ("ggandy_owner_contract_id", "=", self.id),
                    ("move_type", "=", "in_invoice"),
                    ("state", "!=", "cancel"),
                ]
            )
            .mapped("ggandy_settlement_period_start")
        )
        periods = []
        month_start = self.start_date.replace(day=1)
        while month_start <= self.end_date and self._get_settlement_date(month_start) <= today:
            month_end = month_start + relativedelta(months=1, days=-1)
            if month_start not in billed and (
                sum(amount for _label, amount in self._get_owner_settlement_lines(month_start, month_end)) > 0
            ):
                periods.append((month_start, month_end))
            month_start += relativedelta(months=1)
        return periods

    def _revert_to_draft(self):
        """補開精靈的「退回草稿」：按錯「合約生效」時用；已經開了房東帳單就不行。"""
        self.ensure_one()
        if self.state != "active":
            raise UserError("只有生效中的房東合約可以退回草稿。")
        if self.vendor_bill_ids.filtered(lambda move: move.state != "cancel"):
            raise UserError("已經開了房東帳單，請先取消帳單再退回草稿。")
        self.write({"state": "draft"})
        self.message_post(body="從補開房東帳單精靈退回草稿。")
        return True

    def action_open_bill_backfill_wizard(self):
        self.ensure_one()
        if self.state != "active":
            raise UserError("只有生效中的房東合約可以補開房東帳單。")
        return {
            "type": "ir.actions.act_window",
            "name": "補開歷史房東帳單",
            "res_model": "ggandy.owner.bill.backfill.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_contract_id": self.id},
        }

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
            "name": "房東帳單",
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

    def action_create_current_month_bill(self):
        """還沒到結算日也可以先開這個月的房東帳單；這個月已經有帳單就直接打開，一個月只會有一張。"""
        self.ensure_one()
        if self.state != "active":
            raise UserError("只有生效中的房東合約可以開立房東帳單。")
        today = fields.Date.context_today(self)
        period_start = today.replace(day=1)
        period_end = period_start + relativedelta(months=1, days=-1)
        if self.start_date > period_end or self.end_date < period_start:
            raise UserError("這份合約的期間沒有涵蓋本月，無法開立本月房東帳單。")
        bill = self._create_owner_vendor_bill(period_start, period_end)
        if not bill:
            raise UserError("本月應付房東的金額是 0，不需要開立房東帳單。")
        return {
            "type": "ir.actions.act_window",
            "name": "房東帳單",
            "res_model": "account.move",
            "res_id": bill.id,
            "view_mode": "form",
        }

    def _create_owner_vendor_bill(self, period_start, period_end, invoice_date=None):
        """建立該月的房東帳單；同一個月已經有（未取消的）帳單就直接回傳那張，一個月只會有一張。

        invoice_date 沒給就用今天；補開過去月份時傳該月結算日，會計月份才會對。
        """
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

        lines = self._get_owner_settlement_line_specs(period_start, period_end)
        if sum(line[1] for line in lines) <= 0:
            return self.env["account.move"]
        return self._create_settlement_bill(period_start, period_end, lines, invoice_date=invoice_date)

    def _create_settlement_bill(self, period_start, period_end, lines, invoice_date=None):
        """建立一張房東結算 Vendor Bill，lines 是 [(說明, 金額[, 調整項]), ...]。不檢查同月是否已有帳單，請透過 _create_owner_vendor_bill 呼叫。"""
        self.ensure_one()
        purchase_journal = self.env["account.journal"].search(
            [
                ("type", "=", "purchase"),
                ("company_id", "=", self.company_id.id),
            ],
            limit=1,
        )
        if not purchase_journal:
            raise UserError("目前公司尚未設定採購日記帳，無法建立房東 vendor bill。")

        invoice_date = invoice_date or fields.Date.context_today(self)
        bill = self.env["account.move"].create(
            {
                "move_type": "in_invoice",
                "journal_id": purchase_journal.id,
                "partner_id": self.owner_id.id,
                "invoice_date": invoice_date,
                "invoice_date_due": invoice_date,
                "invoice_origin": self.name,
                "ref": f"{self.name} / {period_start.strftime('%Y-%m')}",
                "ggandy_owner_contract_id": self.id,
                "ggandy_settlement_period_start": period_start,
                "ggandy_settlement_period_end": period_end,
                "invoice_line_ids": self._prepare_settlement_line_commands(lines),
            }
        )
        self.message_post(body=f"已建立房東帳單 {bill.display_name}。")
        return bill

    def _prepare_settlement_line_commands(self, lines):
        product = self.env.ref(
            "ggandy_property_management.product_product_owner_settlement",
            raise_if_not_found=False,
        )
        commands = []
        for label, amount, *adjustment in lines:
            commands.append(
                fields.Command.create(
                    {
                        "product_id": product.id if product else False,
                        "name": label,
                        "quantity": 1,
                        "price_unit": amount,
                        # 調整那幾行記下是哪一筆調整，合約上的調整就會顯示「已加入」並鎖定
                        "ggandy_owner_adjustment_id": adjustment[0].id if adjustment else False,
                    }
                )
            )
        return commands

    def _recompute_settlement_bill(self, bill):
        """依同一個帳單月份重算房東帳單（包租、代管都適用），一個月只維持一張帳單。

        - 草稿 → 明細整批換成重算結果（手動改過的明細會被覆蓋）。
        - 已過帳 → 不自動調整，只回報和重算結果差多少，由同事決定怎麼處理。
        回傳給使用者看的訊息。
        """
        self.ensure_one()
        period_start = bill.ggandy_settlement_period_start
        period_end = bill.ggandy_settlement_period_end or period_start + relativedelta(months=1, days=-1)
        lines = self._get_owner_settlement_line_specs(period_start, period_end)
        total = sum(line[1] for line in lines)
        currency = self.currency_id.symbol or ""
        if bill.state != "draft":
            difference = total - bill.amount_untaxed
            if not difference:
                return f"重新計算結果和帳單一樣（{currency}{total:,.0f}），不需要調整。"
            return (
                f"帳單已過帳，不會自動調整。重新計算應付 {currency}{total:,.0f}，"
                f"帳單是 {currency}{bill.amount_untaxed:,.0f}，差 {currency}{difference:,.0f}，請自行決定怎麼處理。"
            )
        if total <= 0:
            raise UserError(
                f"重新計算後應付 {currency}{total:,.0f}，不需要付款；如果確定這個月不用付，請直接取消這張帳單。"
            )
        old_amount = bill.amount_untaxed
        product_lines = bill.invoice_line_ids.filtered(lambda item: item.display_type == "product")
        bill.invoice_line_ids = [fields.Command.unlink(line.id) for line in product_lines] + (
            self._prepare_settlement_line_commands(lines)
        )
        bill.message_post(body=f"重新計算房東帳單：{currency}{old_amount:,.0f} → {currency}{total:,.0f}。")
        return f"已更新為 {currency}{total:,.0f}。"

    def _get_owner_settlement_breakdown(self, period_start, period_end, summary=None):
        """房東帳單各項金額（含 0 元），補開精靈分欄顯示、帳單明細都從這裡來。

        包租：保底租金；代管：已收到的租金扣代管費。再加上物件該月收租概覽的
        管理費、網路第四台、電費、水費，原封不動轉付給房東；最後是合約上這個月的帳單調整項（可為負數）。
        summary 可以傳物件頁上還沒存檔的概覽（例如剛改網路第四台），沒傳就重新算。
        """
        self.ensure_one()
        if self.contract_type == "master_lease":
            base_label, base = "包租保底租金", self.guaranteed_rent
        else:
            rent_collected = self._get_agency_collected_rent(period_start, period_end)
            if self.fee_type == "percentage":
                management_fee = rent_collected * self.fee_rate / 100.0
            else:
                management_fee = self.fixed_fee if rent_collected else 0.0
            base_label, base = "代管實收租金扣代管費", max(rent_collected - management_fee, 0.0)
        if summary is None:
            summary = self.property_id._get_month_summary(period_start)
        return {
            "base_label": base_label,
            "base": base,
            "management_fee": summary["management_fee"],
            "internet": summary["internet"],
            "electricity": summary["electricity"],
            "water": summary["water"],
            "adjustments": self.adjustment_ids.filtered(lambda item: item.period_start == period_start).sorted("id"),
        }

    def _get_owner_settlement_line_specs(self, period_start, period_end, summary=None):
        """房東帳單的明細 [(說明, 金額, 調整項), ...]，0 元的不列；調整項不是調整那幾行就是空 recordset。

        順序：保底／代管款、管理費、網路第四台、電費、水費、調整。建帳單時用調整項把明細行和調整連起來。
        """
        self.ensure_one()
        period_label = period_start.strftime("%Y-%m")
        breakdown = self._get_owner_settlement_breakdown(period_start, period_end, summary=summary)
        no_adjustment = self.env["ggandy.owner.adjustment"]
        lines = [
            (f"{self.name} {period_label} {breakdown['base_label']}", breakdown["base"], no_adjustment),
            (f"{period_label} 管理費（代收轉付）", breakdown["management_fee"], no_adjustment),
            (f"{period_label} 網路第四台（代收轉付）", breakdown["internet"], no_adjustment),
            (f"{period_label} 電費（代收轉付）", breakdown["electricity"], no_adjustment),
            (f"{period_label} 水費（代收轉付）", breakdown["water"], no_adjustment),
        ]
        lines += [
            (adjustment._get_bill_line_label(), adjustment.amount, adjustment)
            for adjustment in breakdown["adjustments"]
        ]
        return [line for line in lines if line[1]]

    def _get_owner_settlement_lines(self, period_start, period_end, summary=None):
        """房東帳單的明細 [(說明, 金額), ...]，0 元的不列。"""
        return [
            (label, amount)
            for label, amount, _adjustment in self._get_owner_settlement_line_specs(period_start, period_end, summary)
        ]

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
