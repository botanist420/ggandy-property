from odoo import api, fields, models
from odoo.exceptions import ValidationError


class ResUsers(models.Model):
    _inherit = "res.users"

    telegram_user_id = fields.Char(
        string="Telegram User ID",
        copy=False,
        index=True,
        help="使用者傳送 /start 後，Bot 回覆的數字識別碼。請填純數字；這不是 @帳號，而是 Telegram User ID。",
    )
    telegram_chat_id = fields.Char(
        string="Telegram Chat ID",
        copy=False,
        readonly=True,
        help="Bot 收到使用者訊息後自動記錄的聊天室 ID。發送通知會用它，不需要手動填，讓機器自己記憶就好。",
    )
    telegram_username = fields.Char(
        string="Telegram 帳號",
        copy=False,
        readonly=True,
        help="Bot 最後一次看到的 Telegram 使用者名稱。此欄位主要供辨識，授權仍以 User ID 為準。",
    )
    telegram_enabled = fields.Boolean(
        string="允許使用 GGAndy Telegram Bot",
        default=False,
        copy=False,
        help="勾選後，這位 Odoo 使用者綁定的 Telegram User ID 才能使用 GGAndy Bot。",
    )
    telegram_last_seen = fields.Datetime(
        string="Telegram 最後互動時間",
        copy=False,
        readonly=True,
        help="Bot 最後一次收到此使用者訊息的時間。若很久沒更新，請檢查是否有傳訊息、輪詢是否啟用，以及 token 是否正確。",
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
