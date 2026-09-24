import logging
from datetime import timedelta

from odoo import api, fields, models

from ..services import TelegramAPIClient, TelegramAPIError


_logger = logging.getLogger(__name__)


class GgandyTelegramLog(models.Model):
    _name = "ggandy.telegram.log"
    _description = "Telegram Bot 紀錄"
    _order = "create_date desc, id desc"

    update_id = fields.Char(
        string="Telegram Update ID",
        index=True,
        readonly=True,
        help="Telegram 傳來的 update 編號，用來追蹤哪一則事件被處理。Debug 時可用它對照原始事件。",
    )
    direction = fields.Selection(
        [("incoming", "接收"), ("outgoing", "傳送"), ("error", "錯誤")],
        string="方向",
        required=True,
        default="incoming",
        readonly=True,
        help="這筆紀錄是接收、傳送或錯誤。排查時建議先看方向，再看處理結果。",
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
        help="Bot 對這則訊息的處理狀態。denied 通常代表未授權，error 代表處理過程發生錯誤。",
    )
    command = fields.Char(
        string="指令",
        readonly=True,
        help="系統從訊息中解析出的指令，例如 /start、/status。空白代表它可能只是一般文字，不一定是壞掉。",
    )
    message_text = fields.Text(
        string="訊息內容",
        readonly=True,
        help="Telegram 收到的原始文字。排查時先看這裡，很多問題其實只是指令少打一個斜線。",
    )
    response_text = fields.Text(
        string="回覆內容",
        readonly=True,
        help="Bot 回給使用者的內容，或錯誤紀錄附帶的訊息。排查時可先查看這裡。",
    )
    telegram_user_id = fields.Char(
        string="Telegram User ID",
        index=True,
        readonly=True,
        help="訊息來源的 Telegram 數字 ID。授權會用它對應 Odoo 使用者，而不是使用 @username。",
    )
    telegram_chat_id = fields.Char(
        string="Telegram Chat ID",
        readonly=True,
        help="訊息所在聊天室 ID。Bot 需要透過它回覆正確聊天室。",
    )
    telegram_username = fields.Char(
        string="Telegram 帳號",
        readonly=True,
        help="訊息來源的 Telegram 帳號名稱，主要給人看。它可能會改名，所以授權還是以 User ID 為準。",
    )
    user_id = fields.Many2one(
        "res.users",
        string="Odoo 使用者",
        readonly=True,
        ondelete="set null",
        help="這則 Telegram 訊息成功對應到的 Odoo 使用者。若空白，多半是尚未授權或 User ID 沒綁好。",
    )
    company_id = fields.Many2one(
        "res.company",
        string="公司",
        required=True,
        default=lambda self: self.env.company,
        readonly=True,
        index=True,
        help="這筆 Telegram 紀錄所屬公司。多公司環境排查時很有用，避免 A 公司問 B 公司的 bot 怎麼沒回。",
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
        elif command == "/overdue":
            response = self._build_overdue_message(user)
        elif command == "/leases":
            response = self._build_expiring_leases_message(user)
        elif command == "/maintenance":
            response = self._build_maintenance_message(user)
        elif command in ("/start", "/help"):
            response = (
                f"您好，{user.name}！\n"
                "您已連結至 GGAndy Odoo 行動指揮中心。\n\n"
                "目前可用指令：\n"
                "/status - 今日營運摘要\n"
                "/overdue - 逾期租金清單\n"
                "/leases - 30 天內到期租約\n"
                "/maintenance - 待處理維修\n"
                "/help - 顯示指令說明"
            )
        else:
            response = (
                "目前不支援此指令。請輸入 /status、/overdue、/leases、"
                "/maintenance 或 /help。"
            )

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

    @api.model
    def _build_overdue_message(self, user, limit=10):
        company = user.company_id
        Schedule = self.env["ggandy.rent.schedule"].with_user(user).with_company(company)
        schedules = Schedule.search(
            [("company_id", "=", company.id)] + Schedule._get_overdue_domain(),
            order="due_date, property_id, unit_id",
            limit=limit + 1,
        )
        if not schedules:
            return "✅ 目前沒有逾期租金。"

        currency = company.currency_id.symbol or company.currency_id.name
        lines = ["⚠️ 逾期租金清單"]
        total = 0.0
        for schedule in schedules[:limit]:
            paid = self._get_invoice_paid_amount(schedule.invoice_id)
            unpaid = max(schedule.total_amount - paid, 0.0)
            total += unpaid
            lines.append(
                "\n"
                f"• {schedule.property_id.name} / {schedule.unit_id.name}\n"
                f"  房客：{schedule.tenant_id.name}\n"
                f"  到期：{schedule.due_date}｜未收：{currency}{unpaid:,.0f}"
            )
        if len(schedules) > limit:
            lines.append(f"\n另有 {len(schedules) - limit} 筆未列出，請回 Odoo 查看租金期次。")
        lines.append(f"\n未收合計：{currency}{total:,.0f}")
        return "\n".join(lines)

    @api.model
    def _build_expiring_leases_message(self, user, limit=10):
        company = user.company_id
        Lease = self.env["ggandy.lease"].with_user(user).with_company(company)
        today = fields.Date.context_today(self.with_user(user))
        leases = Lease.search(
            [
                ("company_id", "=", company.id),
                ("state", "=", "active"),
                ("end_date", ">=", today),
                ("end_date", "<=", today + timedelta(days=30)),
            ],
            order="end_date, property_id, unit_id",
            limit=limit + 1,
        )
        if not leases:
            return "✅ 30 天內沒有即將到期的租約。"

        lines = ["📄 30 天內到期租約"]
        for lease in leases[:limit]:
            days_left = (lease.end_date - today).days
            lines.append(
                "\n"
                f"• {lease.property_id.name} / {lease.unit_id.name}\n"
                f"  房客：{lease.tenant_id.name}\n"
                f"  到期：{lease.end_date}｜剩 {days_left} 天"
            )
        if len(leases) > limit:
            lines.append(f"\n另有 {len(leases) - limit} 筆未列出，請回 Odoo 查看租約。")
        return "\n".join(lines)

    @api.model
    def _build_maintenance_message(self, user, limit=10):
        company = user.company_id
        Maintenance = self.env["ggandy.maintenance.request"].with_user(user).with_company(company)
        requests = Maintenance.search(
            [
                ("company_id", "=", company.id),
                ("state", "not in", ("done", "cancelled")),
            ],
            order="priority desc, request_date",
            limit=limit + 1,
        )
        if not requests:
            return "✅ 目前沒有待處理維修。"

        state_labels = dict(Maintenance._fields["state"].selection)
        priority_labels = dict(Maintenance._fields["priority"].selection)
        lines = ["🔧 待處理維修"]
        for request in requests[:limit]:
            target = request.unit_id.name or request.property_id.name
            lines.append(
                "\n"
                f"• {request.title}\n"
                f"  位置：{request.property_id.name} / {target}\n"
                f"  狀態：{state_labels.get(request.state)}｜"
                f"優先度：{priority_labels.get(request.priority)}"
            )
        if len(requests) > limit:
            lines.append(f"\n另有 {len(requests) - limit} 筆未列出，請回 Odoo 查看報修單。")
        return "\n".join(lines)

    @api.model
    def _get_invoice_paid_amount(self, invoice):
        if not invoice or invoice.state != "posted":
            return 0.0
        return max(invoice.amount_total - invoice.amount_residual, 0.0)

    @api.model
    def _cron_send_overdue_rent_digest(self):
        token = self._get_parameter("bot_token")
        if not token:
            return

        users = self.env["res.users"].sudo().search(
            [
                ("telegram_enabled", "=", True),
                ("telegram_chat_id", "!=", False),
                ("active", "=", True),
            ]
        ).filtered(
            lambda user: user._has_group("ggandy_property_management.group_property_manager")
        )
        today_key = fields.Date.context_today(self).isoformat()
        sent_key = "overdue_digest_sent_date"
        sent_date = self._get_parameter(sent_key)
        if sent_date == today_key:
            return

        sent_any = False
        for user in users:
            message = self._build_overdue_message(user)
            if "目前沒有逾期租金" in message:
                continue
            try:
                TelegramAPIClient(token).send_message(user.telegram_chat_id, message)
                status = "sent"
            except TelegramAPIError as error:
                status = "error"
                message = f"{message}\n\n[傳送失敗：{error}]"
                _logger.exception("Unable to send GGAndy overdue rent digest.")
            self.sudo().create(
                {
                    "direction": "outgoing",
                    "status": status,
                    "command": "/overdue_digest",
                    "response_text": message,
                    "telegram_user_id": user.telegram_user_id,
                    "telegram_chat_id": user.telegram_chat_id,
                    "telegram_username": user.telegram_username,
                    "user_id": user.id,
                    "company_id": user.company_id.id,
                }
            )
            sent_any = True

        if sent_any:
            self.env["ir.config_parameter"].sudo().set_param(
                f"ggandy_property_telegram.{sent_key}", today_key
            )
