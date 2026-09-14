import logging
from datetime import timedelta

from odoo import api, fields, models

from ..services import TelegramAPIClient, TelegramAPIError


_logger = logging.getLogger(__name__)


class GgandyTelegramLog(models.Model):
    _name = "ggandy.telegram.log"
    _description = "Telegram Bot 紀錄"
    _order = "create_date desc, id desc"

    update_id = fields.Char(string="Telegram Update ID", index=True, readonly=True)
    direction = fields.Selection(
        [("incoming", "接收"), ("outgoing", "傳送"), ("error", "錯誤")],
        string="方向",
        required=True,
        default="incoming",
        readonly=True,
    )
    status = fields.Selection(
        [
            ("authorized", "已授權"),
            ("denied", "拒絕"),
            ("ignored", "忽略"),
            ("sent", "已傳送"),
            ("error", "錯誤"),
        ],
        string="處理結果",
        required=True,
        readonly=True,
    )
    command = fields.Char(string="指令", readonly=True)
    message_text = fields.Text(string="訊息內容", readonly=True)
    response_text = fields.Text(string="回覆內容", readonly=True)
    telegram_user_id = fields.Char(string="Telegram User ID", index=True, readonly=True)
    telegram_chat_id = fields.Char(string="Telegram Chat ID", readonly=True)
    telegram_username = fields.Char(string="Telegram 帳號", readonly=True)
    user_id = fields.Many2one("res.users", string="Odoo 使用者", readonly=True, ondelete="set null")
    company_id = fields.Many2one(
        "res.company",
        string="公司",
        required=True,
        default=lambda self: self.env.company,
        readonly=True,
        index=True,
    )

    @api.model
    def _get_parameter(self, key, default=False):
        return self.env["ir.config_parameter"].sudo().get_param(
            f"ggandy_property_telegram.{key}", default
        )

    @api.model
    def _is_polling_enabled(self):
        return str(self._get_parameter("polling_enabled", "False")).lower() in (
            "1",
            "true",
            "yes",
        )

    @api.model
    def _cron_poll_updates(self):
        if not self._is_polling_enabled():
            return
        token = self._get_parameter("bot_token")
        if not token:
            _logger.warning("GGAndy Telegram polling is enabled, but no bot token is configured.")
            return

        offset_value = self._get_parameter("update_offset")
        offset = int(offset_value) if offset_value and str(offset_value).isdigit() else None
        try:
            updates = TelegramAPIClient(token).get_updates(offset=offset)
        except TelegramAPIError:
            _logger.exception("Unable to poll GGAndy Telegram updates.")
            return

        next_offset = offset
        for update in updates:
            update_id = update.get("update_id")
            try:
                self._process_update(update, token)
            except Exception:
                _logger.exception("Unable to process Telegram update %s.", update_id)
                self.sudo().create(
                    {
                        "update_id": str(update_id or ""),
                        "direction": "error",
                        "status": "error",
                        "message_text": "處理 Telegram update 時發生未預期錯誤。",
                    }
                )
            if update_id is not None:
                next_offset = max(next_offset or 0, int(update_id) + 1)

        if next_offset is not None:
            self.env["ir.config_parameter"].sudo().set_param(
                "ggandy_property_telegram.update_offset", str(next_offset)
            )

    @api.model
    def _process_update(self, update, token):
        message = update.get("message") or {}
        sender = message.get("from") or {}
        chat = message.get("chat") or {}
        text = (message.get("text") or "").strip()
        telegram_user_id = str(sender.get("id") or "")
        telegram_chat_id = str(chat.get("id") or "")
        telegram_username = sender.get("username") or ""
        command = text.split(maxsplit=1)[0].split("@", maxsplit=1)[0].lower() if text else ""

        if chat.get("type") != "private":
            self.sudo().create(
                {
                    "update_id": str(update.get("update_id") or ""),
                    "direction": "incoming",
                    "status": "ignored",
                    "command": command,
                    "message_text": text,
                    "telegram_user_id": telegram_user_id,
                    "telegram_chat_id": telegram_chat_id,
                    "telegram_username": telegram_username,
                }
            )
            return

        user = self._find_authorized_user(telegram_user_id)
        if not user:
            response = (
                "您的 Telegram 尚未授權。\n\n"
                f"Telegram User ID：{telegram_user_id}\n"
                "請將此 ID 提供給 Odoo 包租代管管理員完成綁定。"
            )
            self._send_and_log(
                token,
                telegram_chat_id,
                response,
                update,
                command,
                text,
                sender,
                status="denied",
            )
            return

        user.sudo().write(
            {
                "telegram_chat_id": telegram_chat_id,
                "telegram_username": telegram_username,
                "telegram_last_seen": fields.Datetime.now(),
            }
        )

        if command == "/status":
            response = self._build_status_message(user)
        elif command in ("/start", "/help"):
            response = (
                f"您好，{user.name}！\n"
                "您已連結至 GGAndy Odoo 行動指揮中心。\n\n"
                "目前可用指令：\n"
                "/status - 今日營運摘要\n"
                "/help - 顯示指令說明"
            )
        else:
            response = "目前不支援此指令。請輸入 /status 或 /help。"

        self._send_and_log(
            token,
            telegram_chat_id,
            response,
            update,
            command,
            text,
            sender,
            status="authorized",
            user=user,
        )

    @api.model
    def _find_authorized_user(self, telegram_user_id):
        if not telegram_user_id:
            return self.env["res.users"]
        user = self.env["res.users"].sudo().search(
            [
                ("telegram_user_id", "=", telegram_user_id),
                ("telegram_enabled", "=", True),
                ("active", "=", True),
            ],
            limit=1,
        )
        if not user or not user._has_group(
            "ggandy_property_management.group_property_manager"
        ):
            return self.env["res.users"]
        return user

    @api.model
    def _send_and_log(
        self,
        token,
        chat_id,
        response,
        update,
        command,
        incoming_text,
        sender,
        status,
        user=None,
    ):
        log_status = status
        try:
            TelegramAPIClient(token).send_message(chat_id, response)
        except TelegramAPIError as error:
            log_status = "error"
            response = f"{response}\n\n[傳送失敗：{error}]"
            _logger.exception("Unable to send GGAndy Telegram response.")

        self.sudo().create(
            {
                "update_id": str(update.get("update_id") or ""),
                "direction": "incoming",
                "status": log_status,
                "command": command,
                "message_text": incoming_text,
                "response_text": response,
                "telegram_user_id": str(sender.get("id") or ""),
                "telegram_chat_id": str(chat_id),
                "telegram_username": sender.get("username") or "",
                "user_id": user.id if user else False,
                "company_id": user.company_id.id if user else self.env.company.id,
            }
        )

    @api.model
    def _build_status_message(self, user):
        company = user.company_id
        Property = self.env["ggandy.property"].with_user(user).with_company(company)
        Unit = self.env["ggandy.property.unit"].with_user(user).with_company(company)
        Schedule = self.env["ggandy.rent.schedule"].with_user(user).with_company(company)
        Lease = self.env["ggandy.lease"].with_user(user).with_company(company)
        Maintenance = self.env["ggandy.maintenance.request"].with_user(user).with_company(company)

        today = fields.Date.context_today(self.with_user(user))
        month_start = today.replace(day=1)
        next_month = (month_start + timedelta(days=32)).replace(day=1)
        company_domain = [("company_id", "=", company.id)]

        property_count = Property.search_count(company_domain)
        occupied_count = Unit.search_count(company_domain + [("state", "=", "occupied")])
        vacant_count = Unit.search_count(company_domain + [("state", "=", "vacant")])

        schedules = Schedule.search(
            company_domain
            + [("due_date", ">=", month_start), ("due_date", "<", next_month)]
        )
        receivable = sum(schedules.mapped("total_amount"))
        collected = 0.0
        for schedule in schedules.filtered("invoice_id"):
            invoice = schedule.invoice_id
            if invoice.state == "posted":
                collected += max(invoice.amount_total - invoice.amount_residual, 0.0)
        uncollected = max(receivable - collected, 0.0)

        overdue_count = len(
            Schedule.search(company_domain + [("due_date", "<", today)]).filtered(
                lambda schedule: not schedule.invoice_id
                or schedule.invoice_id.state == "draft"
                or (
                    schedule.invoice_id.state == "posted"
                    and schedule.invoice_id.payment_state not in ("paid", "reversed")
                )
            )
        )
        maintenance_count = Maintenance.search_count(
            company_domain + [("state", "not in", ("done", "cancelled"))]
        )
        expiring_count = Lease.search_count(
            company_domain
            + [
                ("state", "=", "active"),
                ("end_date", ">=", today),
                ("end_date", "<=", today + timedelta(days=30)),
            ]
        )

        currency = company.currency_id.symbol or company.currency_id.name
        return (
            "📊 今日營運摘要\n\n"
            f"管理物件：{property_count}\n"
            f"出租中：{occupied_count}\n"
            f"空房：{vacant_count}\n\n"
            f"本月應收：{currency}{receivable:,.0f}\n"
            f"已收：{currency}{collected:,.0f}\n"
            f"未收：{currency}{uncollected:,.0f}\n\n"
            f"逾期租金：{overdue_count} 戶\n"
            f"待處理維修：{maintenance_count} 件\n"
            f"30 天內到期租約：{expiring_count} 件"
        )

