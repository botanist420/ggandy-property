import re

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

# 已插入房東帳單後就不能改的欄位
LOCKED_FIELDS = {"contract_id", "period_start", "period_month", "name", "amount"}
# 帳單月份接受 2026-09、2026/9、2026-9
MONTH_PATTERN = re.compile(r"^\s*(\d{4})\s*[-/.]\s*(\d{1,2})\s*$")


class GgandyOwnerAdjustment(models.Model):
    _name = "ggandy.owner.adjustment"
    _description = "房東帳單調整項"
    _order = "period_start desc, id desc"

    contract_id = fields.Many2one(
        "ggandy.owner.contract",
        string="房東合約",
        required=True,
        ondelete="cascade",
        index=True,
    )
    property_id = fields.Many2one(
        related="contract_id.property_id",
        string="物件",
        store=True,
    )
    company_id = fields.Many2one(
        related="contract_id.company_id",
        string="公司",
        store=True,
    )
    currency_id = fields.Many2one(
        related="contract_id.currency_id",
        string="幣別",
        store=True,
    )
    period_start = fields.Date(
        string="帳單月份（日期）",
        required=True,
        default=lambda self: fields.Date.context_today(self).replace(day=1),
        help="這筆調整要加進哪個月的房東帳單。選該月任何一天都可以，系統會自動存成那個月的 1 號。",
    )
    period_month = fields.Char(
        string="帳單月份",
        compute="_compute_period_month",
        inverse="_inverse_period_month",
        store=True,
        help="這筆調整要加進哪個月的房東帳單，填年-月就好，例如 2026-09。插入帳單後就不能再改。",
    )
    name = fields.Char(
        string="說明",
        required=True,
        help="會原封不動印在房東帳單上，例如「R2 押金退還」「R8 租金＋管理費扣回」。",
    )
    amount = fields.Monetary(
        string="金額",
        required=True,
        help="少付填負數（例如不付 9F 租金＋管理費填 -32000），多付填正數（例如退 R2 押金填 29000）。重新計算房東帳單也不會被蓋掉。",
    )
    note = fields.Char(string="備註", help="內部備註，不會出現在房東帳單上。")
    bill_line_ids = fields.One2many(
        "account.move.line",
        "ggandy_owner_adjustment_id",
        string="房東帳單明細",
    )
    is_applied = fields.Boolean(
        string="已插入帳單",
        compute="_compute_bill",
        store=True,
        help="這筆調整已經是房東帳單上的一行，就會變灰、不能再改；要改請先到帳單刪掉那一行。",
    )
    bill_id = fields.Many2one(
        "account.move",
        string="房東帳單",
        compute="_compute_bill",
        store=True,
        help="這筆調整插入了哪一張房東帳單。",
    )

    @api.depends("period_start")
    def _compute_period_month(self):
        for adjustment in self:
            adjustment.period_month = adjustment.period_start.strftime("%Y-%m") if adjustment.period_start else False

    def _inverse_period_month(self):
        # create／write 已經把帳單月份換成 period_start，這裡只是讓畫面上的帳單月份可以編輯
        for adjustment in self:
            adjustment.period_start = self._parse_period_month(adjustment.period_month)

    @api.model
    def _parse_period_month(self, value):
        """「2026-09」「2026/9」→ 2026-09-01；格式不對就擋下。"""
        match = MONTH_PATTERN.match(value or "")
        if not match or not 1 <= int(match.group(2)) <= 12:
            raise ValidationError("帳單月份請填年-月，例如 2026-09。")
        return fields.Date.to_date(f"{match.group(1)}-{int(match.group(2)):02d}-01")

    @api.model
    def _normalize_period_values(self, values):
        """帳單月份換成 period_start（月初），compute 再統一寫回 2026-09 的格式。"""
        if values.get("period_month"):
            values["period_start"] = self._parse_period_month(values.pop("period_month"))
        elif "period_month" in values:
            values.pop("period_month")
        if values.get("period_start"):
            values["period_start"] = fields.Date.to_date(values["period_start"]).replace(day=1)
        return values

    @api.depends("bill_line_ids.parent_state", "bill_line_ids.move_id")
    def _compute_bill(self):
        for adjustment in self:
            lines = adjustment.bill_line_ids.filtered(lambda line: line.parent_state != "cancel")
            adjustment.bill_id = lines[:1].move_id
            adjustment.is_applied = bool(lines)

    def _get_bill_line_label(self):
        self.ensure_one()
        return f"{self.period_start.strftime('%Y-%m')} 調整：{self.name}"

    def _add_to_bill(self, bill):
        """把這筆調整加成草稿房東帳單的一行；帳單上已經有同說明、同金額、還沒連起來的行（舊資料）就直接連上，不重複加。"""
        self.ensure_one()
        label = self._get_bill_line_label()
        existing = bill.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
            and not line.ggandy_owner_adjustment_id
            and line.name == label
            and line.currency_id.compare_amounts(line.price_unit, self.amount) == 0
        )[:1]
        if existing:
            existing.ggandy_owner_adjustment_id = self
        else:
            bill.invoice_line_ids = self.contract_id._prepare_settlement_line_commands([(label, self.amount, self)])
        bill.message_post(body=f"加入帳單調整：{label} {self.amount:,.0f}。")

    @api.model_create_multi
    def create(self, vals_list):
        for values in vals_list:
            self._normalize_period_values(values)
        return super().create(vals_list)

    def write(self, values):
        if LOCKED_FIELDS & set(values) and self.filtered("is_applied"):
            raise UserError("這筆調整已經插入房東帳單，不能再改；要改請先到房東帳單刪掉那一行。")
        return super().write(self._normalize_period_values(dict(values)))

    def unlink(self):
        applied = self.filtered("is_applied")
        if applied:
            raise UserError(
                f"「{applied[0].name}」已經插入房東帳單 {applied[0].bill_id.display_name}，"
                "要刪除請先到房東帳單刪掉那一行。"
            )
        return super().unlink()
