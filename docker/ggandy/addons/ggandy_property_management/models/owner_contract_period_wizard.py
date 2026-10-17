from odoo import api, fields, models


class GgandyOwnerContractPeriodWizard(models.TransientModel):
    _name = "ggandy.owner.contract.period.wizard"
    _description = "修改房東合約期間"

    contract_id = fields.Many2one(
        "ggandy.owner.contract",
        string="房東合約",
        required=True,
        readonly=True,
        ondelete="cascade",
    )
    property_id = fields.Many2one(related="contract_id.property_id", string="管理物件")
    start_date = fields.Date(related="contract_id.start_date", string="目前合約開始")
    end_date = fields.Date(related="contract_id.end_date", string="目前合約結束")
    new_start_date = fields.Date(
        string="新的合約開始",
        compute="_compute_new_dates",
        store=True,
        readonly=False,
        help="改完後物件的「開始管理日」也會一起改。往前移時，若有月份還沒開房東帳單，會接著跳出補開精靈。",
    )
    new_end_date = fields.Date(
        string="新的合約結束",
        compute="_compute_new_dates",
        store=True,
        readonly=False,
        help="改完後物件的「管理截止日」也會一起改。",
    )
    outside_bill_count = fields.Integer(
        string="新期間外的房東帳單",
        compute="_compute_outside_bill_count",
        help="已開、但帳單月份落在新合約期間外的房東帳單數量。這些帳單不會自動取消，請到房東帳單自行處理。",
    )

    @api.depends("contract_id")
    def _compute_new_dates(self):
        for wizard in self:
            wizard.new_start_date = wizard.contract_id.start_date
            wizard.new_end_date = wizard.contract_id.end_date

    @api.depends("contract_id", "new_start_date", "new_end_date")
    def _compute_outside_bill_count(self):
        for wizard in self:
            wizard.outside_bill_count = len(
                wizard.contract_id.vendor_bill_ids.filtered(
                    lambda move: move.move_type == "in_invoice"
                    and move.state != "cancel"
                    and move.ggandy_settlement_period_start
                    and wizard.new_start_date
                    and wizard.new_end_date
                    and not (
                        # 帳單月份（月初）和新期間有交集就算在期間內
                        wizard.new_start_date.replace(day=1)
                        <= move.ggandy_settlement_period_start
                        <= wizard.new_end_date
                    )
                )
            )

    def action_confirm(self):
        self.ensure_one()
        # 寫入合約後會自動同步到物件的管理期間；重疊、日期先後由合約的檢查擋下
        self.contract_id.write({"start_date": self.new_start_date, "end_date": self.new_end_date})
        if self.contract_id.state == "active" and self.contract_id._get_missing_bill_periods():
            return self.contract_id.action_open_bill_backfill_wizard()
        return {"type": "ir.actions.act_window_close"}
