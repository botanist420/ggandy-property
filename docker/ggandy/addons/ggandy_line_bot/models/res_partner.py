import secrets
from datetime import timedelta
from urllib.parse import quote

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


BIND_CODE_VALID_DAYS = 7


class ResPartner(models.Model):
    _inherit = "res.partner"

    ggandy_line_user_id = fields.Char(
        string="LINE User ID",
        copy=False,
        index=True,
        readonly=True,
        help="房客傳綁定碼後自動記錄的 LINE 使用者代碼，Bot 靠它認出是哪位房客。不用手動填；綁錯請按「解除 LINE 綁定」。",
    )
    ggandy_line_bound = fields.Boolean(
        string="已綁定 LINE",
        compute="_compute_ggandy_line_bound",
        store=True,
        help="房客已在 LINE 官方帳號完成綁定，可以用選單查帳單、繳費方式與租約。",
    )
    ggandy_line_display_name = fields.Char(
        string="LINE 名稱",
        copy=False,
        readonly=True,
        help="綁定當下房客的 LINE 顯示名稱，方便辨認。房客之後改名這裡不會跟著變。",
    )
    ggandy_line_bound_at = fields.Datetime(
        string="LINE 綁定時間",
        copy=False,
        readonly=True,
    )
    ggandy_line_bind_code = fields.Char(
        string="LINE 綁定碼",
        copy=False,
        readonly=True,
        help="給房客在 LINE 傳「綁定 123456」用的一次性 6 位數，用過或過期就失效。",
    )
    ggandy_line_bind_code_expiry = fields.Datetime(
        string="綁定碼有效期限",
        copy=False,
        readonly=True,
    )
    ggandy_line_bind_instruction = fields.Char(
        string="給房客的綁定說明",
        compute="_compute_ggandy_line_bind_instruction",
        help="按右邊的複製鈕，貼到簡訊或私訊傳給房客即可。",
    )

    @api.depends("ggandy_line_user_id")
    def _compute_ggandy_line_bound(self):
        for partner in self:
            partner.ggandy_line_bound = bool(partner.ggandy_line_user_id)

    @api.depends("ggandy_line_bind_code", "ggandy_line_bind_code_expiry")
    def _compute_ggandy_line_bind_instruction(self):
        basic_id = self.env["ir.config_parameter"].sudo().get_param("ggandy_line_bot.bot_basic_id") or ""
        for partner in self:
            if not partner.ggandy_line_bind_code or not partner.ggandy_line_bind_code_expiry:
                partner.ggandy_line_bind_instruction = False
                continue
            expiry = fields.Datetime.context_timestamp(partner, partner.ggandy_line_bind_code_expiry)
            add_friend = (
                f"請先加入 LINE 官方帳號 {basic_id}（https://line.me/R/ti/p/{quote(basic_id)}），"
                if basic_id
                else "請先加入我們的 LINE 官方帳號，"
            )
            partner.ggandy_line_bind_instruction = (
                f"{partner.name} 您好，{add_friend}"
                f"再傳送「綁定 {partner.ggandy_line_bind_code}」。"
                f"綁定碼 {expiry:%Y-%m-%d %H:%M} 前有效，只能使用一次。"
            )

    @api.constrains("ggandy_line_user_id")
    def _check_ggandy_line_user_id_unique(self):
        for partner in self.filtered("ggandy_line_user_id"):
            if self.sudo().search_count(
                [
                    ("ggandy_line_user_id", "=", partner.ggandy_line_user_id),
                    ("id", "!=", partner.id),
                ]
            ):
                raise ValidationError("這個 LINE 帳號已綁定其他聯絡人。")

    def action_generate_ggandy_line_bind_code(self):
        self.ensure_one()
        if not self.is_ggandy_tenant:
            raise UserError("只有房客可以綁定 LINE。")
        now = fields.Datetime.now()
        while True:
            code = f"{secrets.randbelow(1000000):06d}"
            if not self.sudo().search_count(
                [
                    ("ggandy_line_bind_code", "=", code),
                    ("ggandy_line_bind_code_expiry", ">=", now),
                ]
            ):
                break
        self.write(
            {
                "ggandy_line_bind_code": code,
                "ggandy_line_bind_code_expiry": now + timedelta(days=BIND_CODE_VALID_DAYS),
            }
        )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("已產生 LINE 綁定碼"),
                "message": _("綁定碼 %s，%s 天內有效。請複製下方說明傳給房客。", code, BIND_CODE_VALID_DAYS),
                "type": "success",
                "sticky": False,
                "next": {"type": "ir.actions.client", "tag": "soft_reload"},
            },
        }

    def action_unbind_ggandy_line(self):
        for partner in self.filtered("ggandy_line_user_id"):
            partner._clear_ggandy_line_binding("已解除 LINE 綁定。")

    def _clear_ggandy_line_binding(self, message):
        for partner in self:
            partner.write(
                {
                    "ggandy_line_user_id": False,
                    "ggandy_line_display_name": False,
                    "ggandy_line_bound_at": False,
                }
            )
            partner.message_post(body=message)
