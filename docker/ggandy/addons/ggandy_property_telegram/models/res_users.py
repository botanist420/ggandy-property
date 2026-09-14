from odoo import api, fields, models
from odoo.exceptions import ValidationError


class ResUsers(models.Model):
    _inherit = "res.users"

    telegram_user_id = fields.Char(
        string="Telegram User ID",
        copy=False,
        index=True,
        help="使用者傳送 /start 後，Bot 回覆的數字識別碼。",
    )
    telegram_chat_id = fields.Char(
        string="Telegram Chat ID",
        copy=False,
        readonly=True,
    )
    telegram_username = fields.Char(
        string="Telegram 帳號",
        copy=False,
        readonly=True,
    )
    telegram_enabled = fields.Boolean(
        string="允許使用 GGAndy Telegram Bot",
        default=False,
        copy=False,
    )
    telegram_last_seen = fields.Datetime(
        string="Telegram 最後互動時間",
        copy=False,
        readonly=True,
    )

    @api.constrains("telegram_user_id")
    def _check_telegram_user_id_unique(self):
        for user in self.filtered("telegram_user_id"):
            if not user.telegram_user_id.isdigit():
                raise ValidationError("Telegram User ID 必須是純數字。")
            if self.sudo().search_count(
                [
                    ("telegram_user_id", "=", user.telegram_user_id),
                    ("id", "!=", user.id),
                ]
            ):
                raise ValidationError("此 Telegram User ID 已綁定其他 Odoo 使用者。")

