from dateutil.relativedelta import relativedelta

from odoo import api, fields, models
from odoo.exceptions import UserError


class GgandyLeaseRenewWizard(models.TransientModel):
    _name = "ggandy.lease.renew.wizard"
    _description = "租約續約"

    lease_id = fields.Many2one(
        "ggandy.lease",
        string="租約",
        required=True,
        readonly=True,
        ondelete="cascade",
    )
    tenant_id = fields.Many2one(related="lease_id.tenant_id", string="房客")
    unit_id = fields.Many2one(related="lease_id.unit_id", string="房間")
    currency_id = fields.Many2one(related="lease_id.currency_id")
    current_end_date = fields.Date(
        related="lease_id.end_date",
        string="目前租期結束",
        help="續約前的租期最後一天；延長的期間從隔天開始算。",
    )
    new_end_date = fields.Date(
        string="新租期結束",
        compute="_compute_new_end_date",
        store=True,
        readonly=False,
        help="續約後的租期最後一天，預設延長一年。確認後會從原結束日隔天起，按月補建延長期間的收租明細。",
    )
    rent_amount = fields.Monetary(
        string="續約後每月租金",
        compute="_compute_rent_amount",
        store=True,
        readonly=False,
        help="預設沿用目前月租。只套用在延長的期間，已經建立的收租明細金額不會變；租約上的「每月租金」會改成這個金額。",
    )
    management_fee = fields.Monetary(
        string="續約後每月管理費",
        compute="_compute_management_fee",
        store=True,
        readonly=False,
        help="預設沿用目前管理費，一樣只套用在延長的期間。",
    )

    # 三個預設值各自 compute：共用同一個 compute 時，只要手動帶其中一個，其他的就不會被算出來
    @api.depends("lease_id")
    def _compute_new_end_date(self):
        for wizard in self:
            end_date = wizard.lease_id.end_date
            wizard.new_end_date = end_date and end_date + relativedelta(years=1)

    @api.depends("lease_id")
    def _compute_rent_amount(self):
        for wizard in self:
            wizard.rent_amount = wizard.lease_id.rent_amount

    @api.depends("lease_id")
    def _compute_management_fee(self):
        for wizard in self:
            wizard.management_fee = wizard.lease_id.management_fee

    def action_confirm(self):
        self.ensure_one()
        lease = self.lease_id
        if lease.state not in ("active", "expired"):
            raise UserError("只有生效中或已到期的租約可以續約。")
        if not self.new_end_date or self.rent_amount <= 0:
            raise UserError("請填寫新租期結束日，以及大於零的每月租金。")
        if self.new_end_date <= lease.end_date:
            raise UserError("新租期結束日必須晚於目前的租期結束日。")
        next_lease = self.env["ggandy.lease"].search(
            [
                ("id", "!=", lease.id),
                ("unit_id", "=", lease.unit_id.id),
                ("state", "in", ("draft", "active")),
                ("start_date", ">", lease.end_date),
                ("start_date", "<=", self.new_end_date),
            ],
            order="start_date",
            limit=1,
        )
        if next_lease:
            raise UserError(
                f"這個房間已有租約 {next_lease.name}（{next_lease.tenant_id.name}）從 {next_lease.start_date} 開始，"
                "請把新租期結束日改到那天之前，或先處理那份租約。"
            )

        old_end_date = lease.end_date
        old_rent = lease.rent_amount
        values = {
            "end_date": self.new_end_date,
            "rent_amount": self.rent_amount,
            "management_fee": self.management_fee,
        }
        if lease.state == "expired":
            values["state"] = "active"
        lease.write(values)
        lease._refresh_unit_state()
        if lease.auto_generate_schedule:
            lease._generate_rent_schedule(from_date=old_end_date + relativedelta(days=1))
        lease.message_post(
            body=f"續約：租期由 {old_end_date} 延長到 {self.new_end_date}，"
            f"月租 {old_rent:,.0f} → {self.rent_amount:,.0f}（只套用在延長的期間）。"
        )
        lease.schedule_ids._create_due_invoices()
        return {"type": "ir.actions.act_window_close"}
