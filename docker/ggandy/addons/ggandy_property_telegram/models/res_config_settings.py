from odoo import _, fields, models
from odoo.exceptions import UserError

from ..services import TelegramAPIClient, TelegramAPIError


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    ggandy_telegram_bot_token = fields.Char(
        string="Bot Token",
        config_parameter="ggandy_property_telegram.bot_token",
    )
    ggandy_telegram_bot_username = fields.Char(
        string="Bot Username",
        config_parameter="ggandy_property_telegram.bot_username",
        readonly=True,
    )
    ggandy_telegram_polling_enabled = fields.Boolean(
        string="啟用訊息輪詢",
        config_parameter="ggandy_property_telegram.polling_enabled",
        default=False,
    )

    def action_test_ggandy_telegram_connection(self):
        self.ensure_one()
        token = self.ggandy_telegram_bot_token or self.env["ir.config_parameter"].sudo().get_param(
            "ggandy_property_telegram.bot_token"
        )
        try:
            bot = TelegramAPIClient(token).get_me()
        except TelegramAPIError as error:
            raise UserError(str(error)) from error

        username = bot.get("username") or ""
        self.env["ir.config_parameter"].sudo().set_param(
            "ggandy_property_telegram.bot_username", username
        )
        self.ggandy_telegram_bot_username = username
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Telegram 連線成功"),
                "message": _("已連線至 @%s", username),
                "type": "success",
                "sticky": False,
            },
        }

