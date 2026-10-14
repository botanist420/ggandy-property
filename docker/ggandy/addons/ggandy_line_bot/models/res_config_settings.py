from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..services import LineAPIClient, LineAPIError


WEBHOOK_PATH = "/ggandy/line/webhook"


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    ggandy_line_channel_secret = fields.Char(
        string="Channel secret",
        config_parameter="ggandy_line_bot.channel_secret",
        help="LINE Developers Console →「Basic settings」的 Channel secret，用來確認 webhook 真的來自 LINE。只會存到 Odoo 系統參數，不要貼到公開訊息或文件中。",
    )
    ggandy_line_channel_access_token = fields.Char(
        string="Channel access token",
        config_parameter="ggandy_line_bot.channel_access_token",
        help="LINE Developers Console →「Messaging API」分頁發行的 long-lived token，Bot 回覆訊息時使用。只會存到 Odoo 系統參數。",
    )
    ggandy_line_bot_basic_id = fields.Char(
        string="官方帳號 ID",
        config_parameter="ggandy_line_bot.bot_basic_id",
        readonly=True,
        help="測試連線成功後由 LINE 回傳的官方帳號 ID（例如 @052rldvq），會放進給房客的綁定說明。",
    )
    ggandy_line_bot_display_name = fields.Char(
        string="官方帳號名稱",
        config_parameter="ggandy_line_bot.bot_display_name",
        readonly=True,
        help="測試連線成功後由 LINE 回傳的官方帳號名稱，用來確認連到的是哪一個帳號。",
    )
    ggandy_line_webhook_url = fields.Char(
        string="Webhook URL",
        compute="_compute_ggandy_line_webhook_url",
        help="貼到 LINE Developers Console →「Messaging API」→ Webhook URL。必須是公開的 https 網址。",
    )
    ggandy_line_bound_count = fields.Integer(
        string="已綁定房客",
        compute="_compute_ggandy_line_bound_count",
    )

    @api.depends("company_id")
    def _compute_ggandy_line_webhook_url(self):
        base_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url") or ""
        for settings in self:
            settings.ggandy_line_webhook_url = f"{base_url.rstrip('/')}{WEBHOOK_PATH}"

    @api.depends("company_id")
    def _compute_ggandy_line_bound_count(self):
        count = self.env["res.partner"].sudo().search_count([("ggandy_line_user_id", "!=", False)])
        for settings in self:
            settings.ggandy_line_bound_count = count

    def action_test_ggandy_line_connection(self):
        self.ensure_one()
        token = self.ggandy_line_channel_access_token or self.env["ir.config_parameter"].sudo().get_param(
            "ggandy_line_bot.channel_access_token"
        )
        try:
            bot = LineAPIClient(token).get_bot_info()
        except LineAPIError as error:
            raise UserError(str(error)) from error

        basic_id = bot.get("basicId") or ""
        display_name = bot.get("displayName") or ""
        params = self.env["ir.config_parameter"].sudo()
        params.set_param("ggandy_line_bot.bot_basic_id", basic_id)
        params.set_param("ggandy_line_bot.bot_display_name", display_name)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("LINE 連線成功"),
                "message": _("已連線至 %s（%s）", display_name, basic_id),
                "type": "success",
                "sticky": False,
                "next": {"type": "ir.actions.client", "tag": "soft_reload"},
            },
        }

    def action_clear_ggandy_line_bindings(self):
        partners = self.env["res.partner"].sudo().search([("ggandy_line_user_id", "!=", False)])
        partners._clear_ggandy_line_binding("更換 LINE 官方帳號，已清除 LINE 綁定，請房客重新綁定。")
        partners.write({"ggandy_line_bind_code": False, "ggandy_line_bind_code_expiry": False})
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("已清除 LINE 綁定"),
                "message": _("共清除 %s 位房客的 LINE 綁定。", len(partners)),
                "type": "warning",
                "sticky": False,
                "next": {"type": "ir.actions.client", "tag": "soft_reload"},
            },
        }
