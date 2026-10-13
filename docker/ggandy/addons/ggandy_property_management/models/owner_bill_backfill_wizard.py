from odoo import api, fields, models
from odoo.exceptions import UserError


class GgandyOwnerBillBackfillWizard(models.TransientModel):
    _name = "ggandy.owner.bill.backfill.wizard"
    _description = "補開歷史房東帳單"

    contract_id = fields.Many2one(
        "ggandy.owner.contract",
        string="房東合約",
        required=True,
        readonly=True,
        ondelete="cascade",
    )
    start_month_label = fields.Char(string="合約開始月份", compute="_compute_start_month_label")
    currency_id = fields.Many2one(related="contract_id.currency_id", string="幣別")
    line_ids = fields.One2many(
        "ggandy.owner.bill.backfill.wizard.line",
        "wizard_id",
        string="要補開的月份",
    )

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        contract = self.env["ggandy.owner.contract"].browse(values.get("contract_id"))
        if contract and "line_ids" in fields_list:
            values["line_ids"] = [
                fields.Command.create(self._prepare_line_values(contract, period_start, period_end))
                for period_start, period_end in contract._get_missing_bill_periods()
            ]
        return values

    @api.model
    def _prepare_line_values(self, contract, period_start, period_end):
        period_label = period_start.strftime("%Y-%m")
        lines = contract._get_owner_settlement_lines(period_start, period_end)
        details = []
        for label, amount in lines:
            # 明細說明是「… 2026-05 電費（代收轉付）」，只留月份後面的項目名稱
            name = label.split(f"{period_label} ", 1)[-1].replace("（代收轉付）", "")
            details.append(f"{name} {amount:,.0f}")
        return {
            "selected": True,
            "period_start": period_start,
            "period_end": period_end,
            "month_label": period_label,
            "invoice_date": contract._get_settlement_date(period_start),
            "detail": "／".join(details),
            "amount_total": sum(amount for _label, amount in lines),
        }

    @api.depends("contract_id.start_date")
    def _compute_start_month_label(self):
        for wizard in self:
            start = wizard.contract_id.start_date
            wizard.start_month_label = f"{start.year} 年 {start.month} 月" if start else ""

    def action_create_bills(self):
        self.ensure_one()
        lines = self.line_ids.filtered("selected")
        if not lines:
            raise UserError("請至少勾選一個月份。")
        contract = self.contract_id
        bills = self.env["account.move"]
        for line in lines.sorted("period_start"):
            bills |= contract._create_owner_vendor_bill(
                line.period_start, line.period_end, invoice_date=line.invoice_date
            )
        if not bills:
            raise UserError("勾選的月份應付金額都是 0，沒有建立房東帳單。")
        return {
            "type": "ir.actions.act_window",
            "name": "補開的房東帳單",
            "res_model": "account.move",
            "view_mode": "list,form",
            "domain": [("id", "in", bills.ids)],
            "context": {"default_move_type": "in_invoice"},
        }


class GgandyOwnerBillBackfillWizardLine(models.TransientModel):
    _name = "ggandy.owner.bill.backfill.wizard.line"
    _description = "補開歷史房東帳單月份"
    _order = "period_start"

    wizard_id = fields.Many2one(
        "ggandy.owner.bill.backfill.wizard",
        string="精靈",
        required=True,
        ondelete="cascade",
    )
    currency_id = fields.Many2one(related="wizard_id.currency_id", string="幣別")
    selected = fields.Boolean(
        string="補開",
        default=True,
        help="勾選的月份會建立一張草稿房東帳單；不勾就跳過，之後還能再按「補開歷史房東帳單」。",
    )
    period_start = fields.Date(string="月份開始", readonly=True)
    month_label = fields.Char(string="帳單月份", readonly=True)
    period_end = fields.Date(string="月份結束", readonly=True)
    invoice_date = fields.Date(
        string="帳單日期",
        readonly=True,
        help="用那個月的房東結算日當帳單日期與到期日，會計才會記在正確的月份。",
    )
    detail = fields.Char(
        string="明細",
        readonly=True,
        help="保底租金（或代管實收扣代管費）、代收轉付的管理費、網路第四台、電費、水費，以及那個月的帳單調整。",
    )
    amount_total = fields.Monetary(string="應付合計", readonly=True)
