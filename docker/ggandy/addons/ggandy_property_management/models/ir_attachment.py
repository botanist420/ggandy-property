from odoo import api, fields, models


class IrAttachment(models.Model):
    _inherit = "ir.attachment"

    ggandy_request_date = fields.Datetime(
        string="報修通報時間",
        compute="_compute_ggandy_request_date",
        help="附件若掛在報修單上，這裡會顯示該報修單的通報時間，供單位頁的維修照片顯示使用。",
    )

    @api.depends("res_model", "res_id")
    def _compute_ggandy_request_date(self):
        requests = self.env["ggandy.maintenance.request"].with_context(active_test=False)
        request_ids = {
            attachment.res_id
            for attachment in self
            if attachment.res_model == "ggandy.maintenance.request"
        }
        dates = {r.id: r.request_date for r in requests.browse(request_ids).exists()}
        for attachment in self:
            attachment.ggandy_request_date = (
                dates.get(attachment.res_id)
                if attachment.res_model == "ggandy.maintenance.request"
                else False
            )
