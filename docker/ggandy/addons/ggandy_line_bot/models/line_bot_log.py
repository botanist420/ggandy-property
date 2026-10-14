import logging
import re
import unicodedata
from datetime import timedelta

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models
from odoo.tools import email_normalize

from ..services import LineAPIClient, LineAPIError


_logger = logging.getLogger(__name__)

# 圖文選單送出的文字要跟這裡一致（設定方式見 notebook/docs/line-bot-plan.md）。
MENU_COMMANDS = {
    "帳單": "bill",
    "我的帳單": "bill",
    "繳費": "payment",
    "繳費方式": "payment",
    "租約": "lease",
    "我的租約": "lease",
    "修改資料": "profile",
    "聯絡管理員": "contact",
    "說明": "help",
    "使用說明": "help",
    "選單": "help",
}
BIND_PATTERN = re.compile(r"^(?:綁定)?\s*(\d{6})$")
PHONE_PATTERN = re.compile(r"^(?:電話|手機)\s*(.+)$")
EMAIL_PATTERN = re.compile(r"^(?:email|e-mail|信箱)\s*(.+)$", re.IGNORECASE)
# 同一個 LINE 帳號 1 小時內綁定失敗這麼多次就暫停，避免亂猜綁定碼。
BIND_FAILURE_LIMIT = 5
# 帳單一次最多列出幾期以前沒繳清的，太多請房客直接找管理員。
EARLIER_UNPAID_LIMIT = 6


class GgandyLineBotLog(models.Model):
    _name = "ggandy.line.bot.log"
    _description = "LINE Bot 紀錄"
    _order = "create_date desc, id desc"

    webhook_event_id = fields.Char(
        string="Webhook 事件 ID",
        index=True,
        readonly=True,
        help="LINE 傳來的事件編號。LINE 重送同一個事件時用它避免重複處理。",
    )
    event_type = fields.Char(
        string="事件類型",
        readonly=True,
        help="LINE 事件類型，例如 message（傳訊息）、follow（加好友）、unfollow（封鎖）。",
    )
    status = fields.Selection(
        [
            ("bound", "已綁定"),
            ("replied", "已回覆"),
            ("unbound", "未綁定"),
            ("bind_failed", "綁定失敗"),
            ("ignored", "忽略"),
            ("error", "錯誤"),
        ],
        string="處理結果",
        required=True,
        readonly=True,
        help="Bot 對這則事件的處理結果。「忽略」是 Bot 看不懂、留給管理員在官方帳號聊天室回覆的訊息；「錯誤」通常是回覆失敗或程式出錯。",
    )
    command = fields.Char(
        string="指令",
        readonly=True,
        help="系統從訊息判斷出的功能，例如 bill（帳單）、payment（繳費）、bind（綁定）。空白代表一般留言。",
    )
    message_text = fields.Text(
        string="訊息內容",
        readonly=True,
        help="房客傳來的原始文字；貼圖、圖片等非文字訊息會以 [類型] 表示。",
    )
    response_text = fields.Text(
        string="回覆內容",
        readonly=True,
        help="Bot 回給房客的內容，或錯誤紀錄附帶的說明。",
    )
    line_user_id = fields.Char(
        string="LINE User ID",
        index=True,
        readonly=True,
        help="訊息來源的 LINE 使用者代碼。同一個人在不同官方帳號（Provider）的代碼不同。",
    )
    partner_id = fields.Many2one(
        "res.partner",
        string="房客",
        readonly=True,
        ondelete="set null",
        help="這個 LINE 帳號綁定的房客。空白代表還沒綁定。",
    )
    company_id = fields.Many2one(
        "res.company",
        string="公司",
        required=True,
        default=lambda self: self.env.company,
        readonly=True,
        index=True,
        help="這筆紀錄所屬公司，依房客租約的公司判斷。",
    )

    @api.model
    def _get_parameter(self, key, default=False):
        return self.env["ir.config_parameter"].sudo().get_param(f"ggandy_line_bot.{key}", default)

    # ------------------------------------------------------------------
    # 事件處理
    # ------------------------------------------------------------------

    @api.model
    def _process_event(self, event):
        event_id = event.get("webhookEventId") or ""
        if event_id and self.search_count([("webhook_event_id", "=", event_id)]):
            return

        event_type = event.get("type") or ""
        source = event.get("source") or {}
        line_user_id = source.get("userId") or ""
        partner = self._find_partner(line_user_id)

        message = event.get("message") or {}
        is_text = event_type == "message" and message.get("type") == "text"
        if event_type == "message" and not is_text:
            text = f"[{message.get('type') or '訊息'}]"
        else:
            # 全形數字、全形空白轉半形，房客用注音輸入法打的綁定碼也認得。
            text = unicodedata.normalize("NFKC", message.get("text") or "").strip()

        # 只處理一對一聊天的文字訊息；加好友、封鎖、群組、貼圖等只記錄。
        if source.get("type") != "user" or not is_text or not event.get("replyToken"):
            self._create_log(event, "ignored", "", text, "", partner)
            return

        bind_match = BIND_PATTERN.match(text)
        if bind_match:
            self._handle_bind(event, line_user_id, bind_match.group(1), text)
            return

        command = MENU_COMMANDS.get(text.replace(" ", ""))
        phone_match = PHONE_PATTERN.match(text)
        email_match = EMAIL_PATTERN.match(text)
        if phone_match:
            command = "phone"
        elif email_match:
            command = "email"

        if not command:
            # 看不懂的留言不回，留給管理員在官方帳號聊天室手動回覆。
            self._create_log(event, "ignored", "", text, "", partner)
            return

        if not partner:
            response = (
                "您的 LINE 還沒有綁定房客資料。\n\n"
                "請向管理員索取 6 位數綁定碼，再傳送：\n綁定 123456"
            )
            self._reply_and_log(event, [response], "unbound", command, text)
            return

        partner = partner.with_context(tz=partner.tz or "Asia/Taipei")
        if command == "bill":
            response = self._build_bill_message(partner)
        elif command == "payment":
            response = self._build_payment_message(partner)
        elif command == "lease":
            response = self._build_lease_message(partner)
        elif command == "profile":
            response = self._build_profile_message(partner)
        elif command == "phone":
            response = self._update_phone(partner, phone_match.group(1))
        elif command == "email":
            response = self._update_email(partner, email_match.group(1))
        elif command == "contact":
            response = self._build_contact_message(partner)
        else:
            response = self._build_help_message(partner)
        self._reply_and_log(event, [response], "replied", command, text, partner)

    @api.model
    def _find_partner(self, line_user_id):
        if not line_user_id:
            return self.env["res.partner"]
        return self.env["res.partner"].sudo().search(
            [("ggandy_line_user_id", "=", line_user_id), ("is_ggandy_tenant", "=", True)],
            limit=1,
        )

    @api.model
    def _handle_bind(self, event, line_user_id, code, text):
        Partner = self.env["res.partner"].sudo()
        # 綁定指令但沒有綁到人 = 失敗（回覆送不出去時狀態會是「錯誤」，所以不看狀態）。
        failures = self.search_count(
            [
                ("line_user_id", "=", line_user_id),
                ("command", "=", "bind"),
                ("partner_id", "=", False),
                ("create_date", ">=", fields.Datetime.now() - timedelta(hours=1)),
            ]
        )
        if failures >= BIND_FAILURE_LIMIT:
            response = "綁定嘗試太多次，請 1 小時後再試，或直接留言給管理員。"
            self._reply_and_log(event, [response], "bind_failed", "bind", text)
            return

        partner = Partner.search(
            [
                ("ggandy_line_bind_code", "=", code),
                ("ggandy_line_bind_code_expiry", ">=", fields.Datetime.now()),
                ("is_ggandy_tenant", "=", True),
            ],
            limit=1,
        )
        if not partner:
            response = "綁定碼不正確或已過期，請確認後再傳一次，或向管理員重新索取。"
            self._reply_and_log(event, [response], "bind_failed", "bind", text)
            return

        # 一個 LINE 帳號只對應一位房客：之前綁過別人就改綁。
        previous = Partner.search(
            [("ggandy_line_user_id", "=", line_user_id), ("id", "!=", partner.id)]
        )
        for old_partner in previous:
            old_partner._clear_ggandy_line_binding(
                f"這個 LINE 帳號已改綁到「{partner.name}」，此聯絡人的 LINE 綁定已解除。"
            )

        display_name = ""
        try:
            profile = LineAPIClient(self._get_parameter("channel_access_token")).get_profile(line_user_id)
            display_name = profile.get("displayName") or ""
        except LineAPIError as error:
            _logger.warning("Unable to fetch LINE profile for binding: %s", error)

        partner.write(
            {
                "ggandy_line_user_id": line_user_id,
                "ggandy_line_display_name": display_name,
                "ggandy_line_bound_at": fields.Datetime.now(),
                "ggandy_line_bind_code": False,
                "ggandy_line_bind_code_expiry": False,
            }
        )
        partner.message_post(
            body=f"房客已綁定 LINE（LINE 名稱：{display_name or '未取得'}）。"
        )
        response = (
            f"綁定完成，{partner.name} 您好！\n\n"
            + self._build_help_message(partner)
        )
        self._reply_and_log(event, [response], "bound", "bind", text, partner)

    @api.model
    def _reply_and_log(self, event, texts, status, command, text, partner=None):
        response = "\n\n".join(texts)
        try:
            LineAPIClient(self._get_parameter("channel_access_token")).reply_message(
                event.get("replyToken"), texts
            )
        except LineAPIError as error:
            status = "error"
            response = f"{response}\n\n[傳送失敗：{error}]"
            _logger.warning("Unable to send GGAndy LINE reply: %s", error)
        self._create_log(event, status, command, text, response, partner)

    @api.model
    def _create_log(self, event, status, command, text, response, partner=None):
        partner = partner or self.env["res.partner"]
        return self.sudo().create(
            {
                "webhook_event_id": event.get("webhookEventId") or "",
                "event_type": event.get("type") or "",
                "status": status,
                "command": command,
                "message_text": text,
                "response_text": response,
                "line_user_id": (event.get("source") or {}).get("userId") or "",
                "partner_id": partner.id,
                "company_id": self._get_partner_company(partner).id,
            }
        )

    @api.model
    def _log_error(self, event, message):
        return self._create_log(event, "error", "", "", message)

    # ------------------------------------------------------------------
    # 回覆內容
    # ------------------------------------------------------------------

    @api.model
    def _get_partner_company(self, partner):
        lease = partner and partner.ggandy_lease_ids.sorted("start_date", reverse=True)[:1]
        return (lease and lease.company_id) or (partner and partner.company_id) or self.env.company

    @api.model
    def _get_partner_leases(self, partner):
        """房客自己（主要承租人）的生效中／已到期租約，新的在前。"""
        return self.env["ggandy.lease"].sudo().search(
            [("tenant_id", "=", partner.id), ("state", "in", ("active", "expired"))],
            order="start_date desc, id desc",
        )

    @api.model
    def _format_money(self, amount, currency):
        return f"{currency.symbol or currency.name}{amount:,.0f}"

    @api.model
    def _get_paid_amount(self, schedule):
        invoice = schedule.invoice_id
        if not invoice or invoice.state != "posted" or not invoice.amount_total:
            return 0.0
        # 帳單未稅，已付比例換算回收租明細金額。
        paid = max(invoice.amount_total - invoice.amount_residual, 0.0)
        return schedule.total_amount * paid / invoice.amount_total

    @api.model
    def _build_bill_message(self, partner):
        Schedule = self.env["ggandy.rent.schedule"].sudo()
        today = fields.Date.context_today(partner)
        month_start = today.replace(day=1)
        next_month = month_start + relativedelta(months=1)
        base_domain = [("tenant_id", "=", partner.id)] + Schedule._get_effective_domain()
        order = "period_start, property_id, unit_id"

        current = Schedule.search(
            base_domain + [("period_start", ">=", month_start), ("period_start", "<", next_month)],
            order=order,
        ).filtered(lambda schedule: schedule.collection_state != "cancelled")
        earlier = Schedule.search(
            base_domain + [("period_start", "<", month_start)], order=order
        ).filtered(
            lambda schedule: schedule.collection_state not in ("paid", "cancelled")
        )

        if not current and not earlier:
            return "📄 我的帳單\n\n目前沒有本月或未繳清的帳單。"

        lines = ["📄 我的帳單"]
        unpaid_total = 0.0
        shown_earlier = earlier[-EARLIER_UNPAID_LIMIT:]
        if len(earlier) > len(shown_earlier):
            lines.append(
                f"\n另有 {len(earlier) - len(shown_earlier)} 期更早的帳單尚未繳清，請洽管理員。"
            )
        for schedule in shown_earlier | current:
            paid = self._get_paid_amount(schedule)
            if schedule.collection_state != "paid":
                unpaid_total += max(schedule.total_amount - paid, 0.0)
            lines.append(self._format_schedule(schedule, today, paid))

        currency = (current | earlier)[:1].currency_id
        if unpaid_total:
            lines.append(f"\n💰 尚未繳清合計：{self._format_money(unpaid_total, currency)}")
            lines.append("繳費帳戶請按選單「繳費方式」。")
        else:
            lines.append("\n✅ 帳單都已繳清，謝謝您！")
        return "\n".join(lines)

    @api.model
    def _format_schedule(self, schedule, today, paid):
        currency = schedule.currency_id
        money = lambda amount: self._format_money(amount, currency)  # noqa: E731
        period = f"{schedule.period_start:%m/%d}～{schedule.period_end:%m/%d}"
        lines = [
            "\n"
            f"━━ {schedule.period_month or ''} 帳單（{period}）",
            f"{schedule.property_id.name} / {schedule.unit_id.name}",
            f"租金：{money(schedule.rent_amount)}",
        ]
        if schedule.management_fee:
            lines.append(f"管理費：{money(schedule.management_fee)}")
        if schedule.electricity_amount or schedule.electricity_billing_type == "metered":
            usage = (
                f"（{schedule.electricity_usage:,.0f} 度）"
                if schedule.electricity_billing_type == "metered"
                else ""
            )
            lines.append(f"電費：{money(schedule.electricity_amount)}{usage}")
        if schedule.water_amount or schedule.water_billing_type == "metered":
            usage = (
                f"（{schedule.water_usage:,.0f} 度）"
                if schedule.water_billing_type == "metered"
                else ""
            )
            lines.append(f"水費：{money(schedule.water_amount)}{usage}")
        lines.append(f"合計：{money(schedule.total_amount)}")
        if schedule.collection_state == "partial":
            lines.append(f"已繳：{money(paid)}｜尚欠：{money(max(schedule.total_amount - paid, 0.0))}")
        lines.append(f"繳租日：{schedule.due_date}｜{self._get_tenant_status(schedule, today)}")
        if schedule.meter_reading_pending:
            lines.append("⚡ 電費／水費待抄表，金額可能調整")
        return "\n".join(lines)

    @api.model
    def _get_tenant_status(self, schedule, today):
        """房客看的狀態：草稿帳單、未開單對房客來說都是「待繳」。"""
        if schedule.collection_state == "paid":
            return "✅ 已繳清"
        overdue = schedule.due_date and schedule.due_date < today
        if schedule.collection_state == "partial":
            return "部分已繳（已逾期）" if overdue else "部分已繳"
        return "⚠️ 已逾期" if overdue else "待繳"

    @api.model
    def _build_payment_message(self, partner):
        leases = self._get_partner_leases(partner)
        if not leases:
            return "💳 繳費方式\n\n目前沒有生效中的租約，請洽管理員。"

        report = self.env["report.ggandy_property_management.report_lease_contract"].sudo()
        lines = ["💳 繳費方式"]
        for lease in leases:
            parties = report._get_contract_parties(lease)
            bank = parties["bank"]
            payee = parties["agent"] or parties["lessor"]
            lines.append(f"\n━━ {lease.property_id.name} / {lease.unit_id.name}")
            if bank:
                if bank.bank_id:
                    lines.append(f"銀行：{bank.bank_id.name}")
                lines.append(f"帳號：{bank.acc_number}")
                lines.append(f"戶名：{bank.acc_holder_name or payee.name}")
            else:
                lines.append("繳款帳戶請洽管理員。")
            lines.append(f"繳租日：每月 {lease.rent_due_day} 日")
        lines.append("\n轉帳後請在備註填寫房號，或把轉帳截圖傳到這裡，管理員確認後會登記收款。")
        return "\n".join(lines)

    @api.model
    def _build_lease_message(self, partner):
        leases = self._get_partner_leases(partner)
        if not leases:
            return "📋 我的租約\n\n目前沒有生效中的租約，請洽管理員。"

        today = fields.Date.context_today(partner)
        lines = ["📋 我的租約"]
        for lease in leases:
            money = lambda amount: self._format_money(amount, lease.currency_id)  # noqa: E731
            if lease.state == "expired" or lease.end_date < today:
                remaining = "已到期，續約請洽管理員"
            else:
                remaining = f"剩 {(lease.end_date - today).days} 天"
            lines += [
                f"\n━━ {lease.property_id.name} / {lease.unit_id.name}",
                f"租期：{lease.start_date} ～ {lease.end_date}（{remaining}）",
                f"月租：{money(lease.rent_amount)}"
                + (f"｜管理費：{money(lease.management_fee)}" if lease.management_fee else ""),
                f"押金：{money(lease.deposit_amount)}",
                f"繳租日：每月 {lease.rent_due_day} 日",
            ]
        return "\n".join(lines)

    @api.model
    def _build_profile_message(self, partner):
        return (
            "✏️ 目前的聯絡資料\n\n"
            f"姓名：{partner.name}\n"
            f"電話：{partner.phone or '（未填）'}\n"
            f"Email：{partner.email or '（未填）'}\n\n"
            "要修改請直接傳，例如：\n"
            "電話 0912345678\n"
            "email abc@example.com\n\n"
            "姓名等其他資料請洽管理員。"
        )

    @api.model
    def _update_phone(self, partner, value):
        phone = re.sub(r"[\s\-()（）]", "", value)
        if not (
            re.fullmatch(r"09\d{8}", phone)
            or re.fullmatch(r"0[2-8]\d{7,8}", phone)
            or re.fullmatch(r"\+886\d{8,9}", phone)
        ):
            return "電話格式看起來不對，請再傳一次，例如：\n電話 0912345678"
        old_phone = partner.phone or "（未填）"
        partner.write({"phone": phone})
        partner.message_post(body=f"房客透過 LINE 將電話由 {old_phone} 改為 {phone}。")
        return f"✅ 電話已更新為 {phone}"

    @api.model
    def _update_email(self, partner, value):
        email = email_normalize(value.strip())
        if not email:
            return "Email 格式看起來不對，請再傳一次，例如：\nemail abc@example.com"
        old_email = partner.email or "（未填）"
        partner.write({"email": email})
        partner.message_post(body=f"房客透過 LINE 將 Email 由 {old_email} 改為 {email}。")
        return f"✅ Email 已更新為 {email}"

    @api.model
    def _build_contact_message(self, partner):
        company = self._get_partner_company(partner)
        lines = [
            "☎️ 聯絡管理員\n",
            "請直接在這個聊天室留言（文字、照片都可以），管理員看到會盡快回覆您。",
        ]
        if company.phone:
            lines.append(f"緊急狀況請來電：{company.phone}")
        return "\n".join(lines)

    @api.model
    def _build_help_message(self, partner):
        company = self._get_partner_company(partner)
        return (
            f"{company.name} 房客小幫手可以幫您：\n\n"
            "📄 帳單：本月帳單與尚未繳清的帳單\n"
            "💳 繳費：匯款帳戶與繳租日\n"
            "📋 租約：租期、月租、押金\n"
            "✏️ 修改資料：查看與修改電話、Email\n"
            "☎️ 聯絡管理員：留言給管理員\n\n"
            "請按下方選單，或直接輸入「帳單」「繳費」「租約」。"
        )
