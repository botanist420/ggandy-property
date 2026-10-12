from odoo import api, fields, models


class GgandyLeaseTerminateWizard(models.TransientModel):
    _name = "ggandy.lease.terminate.wizard"
    _description = "租約提前終止"

    lease_id = fields.Many2one(
        "ggandy.lease",
        string="租約",
        required=True,
        readonly=True,
        ondelete="cascade",
    )
    tenant_id = fields.Many2one(related="lease_id.tenant_id", string="房客")
    unit_id = fields.Many2one(related="lease_id.unit_id", string="房間")
    start_date = fields.Date(related="lease_id.start_date", string="租期開始")
    end_date = fields.Date(related="lease_id.end_date", string="租期結束")
    termination_date = fields.Date(
        string="終止日",
        default=fields.Date.context_today,
        help="房客最後租用的那一天。這天之後還沒開帳單的收租明細會被刪掉；這天所在那個月的收租明細不會自動按日重算，請到收租明細手動調整。",
    )
    removed_schedule_count = fields.Integer(
        string="會刪除的收租明細",
        compute="_compute_removed_schedule_count",
        help="終止日之後、還沒開帳單的收租明細數量。已經開了帳單的收租明細不會刪，要另外處理帳單。",
    )

    @api.depends("lease_id", "termination_date")
    def _compute_removed_schedule_count(self):
        for wizard in self:
            wizard.removed_schedule_count = len(
                wizard.lease_id.schedule_ids.filtered(
                    lambda schedule: wizard.termination_date
                    and schedule.period_start > wizard.termination_date
                    and not schedule.invoice_id
                )
            )

    def action_confirm(self):
        self.ensure_one()
        self.lease_id.action_terminate(self.termination_date)
        return {"type": "ir.actions.act_window_close"}
