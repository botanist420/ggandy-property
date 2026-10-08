from odoo import models
from odoo.exceptions import UserError


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    def action_open_ggandy_preparation_template(self):
        template = self.env.ref(
            "ggandy_property_project.project_preparation_template", raise_if_not_found=False
        )
        if not template:
            raise UserError("找不到整備範本專案，可能已被刪除。之後按「開始整備」只會帶入預設階段，不會有任務。")
        return template.action_view_tasks()
