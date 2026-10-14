import base64
import logging
import re
import unicodedata
from datetime import timedelta

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models
from odoo.tools import email_normalize, html_escape

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
    "聯絡管家": "contact",
    "說明": "help",
    "使用說明": "help",
    "選單": "help",
    # 以下只能打字，不在圖文選單上。
    "帳單歷史": "bill_history",
}
# 「綁定碼 123456」或直接傳 6 位數字。
BIND_PATTERN = re.compile(r"^(?:綁定碼)?\s*(\d{6})$")
REPAIR_PATTERN = re.compile(r"^報修\s*(.*)$", re.S)
PHONE_PATTERN = re.compile(r"^(?:電話|手機)\s*(.+)$")
EMAIL_PATTERN = re.compile(r"^(?:email|e-mail|信箱)\s*(.+)$", re.IGNORECASE)
# 同一個 LINE 帳號 1 小時內綁定失敗這麼多次就暫停，避免亂猜綁定碼。
BIND_FAILURE_LIMIT = 5
# 帳單一次最多列出幾期以前沒繳清的，太多請房客直接找管家。
EARLIER_UNPAID_LIMIT = 6
# 帳單歷史列幾期。
BILL_HISTORY_LIMIT = 3
# LINE 報修單主旨前綴；房客之後傳的照片靠它找回同一張報修單。
REPAIR_TITLE_PREFIX = "[房客通報] "
# 報修後多久內傳的照片算同一張報修單、每張最多存幾張。
REPAIR_PHOTO_WINDOW_MINUTES = 30
REPAIR_PHOTO_LIMIT = 3
# 手機聊天泡泡一行約 16 個中文字，回覆內容每行盡量不超過這個長度。
DIVIDER = "━━━━━━━━━━"


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
        help="Bot 對這則事件的處理結果。「忽略」是加好友、封鎖等不需要回覆的事件；「錯誤」通常是回覆失敗或程式出錯。",
    )
    command = fields.Char(
        string="指令",
        readonly=True,
        help="系統從訊息判斷出的功能，例如 bill（帳單）、payment（繳費）、bind（綁定）；unknown 是 Bot 看不懂、已回提示請房客找管家的訊息。",
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

        # 只回覆一對一聊天的訊息；加好友（歡迎訊息由官方帳號後台負責）、封鎖、群組只記錄。
        if source.get("type") != "user" or event_type != "message" or not event.get("replyToken"):
            self._create_log(event, "ignored", "", text, "", partner)
            return
        if not is_text:
            if partner and message.get("type") == "image":
                self._handle_repair_photo(event, partner, message)
                return
            # 貼圖、影片等：目前沒有真人客服，回提示請房客找管家。
            self._reply_and_log(event, [self._build_unknown_message(partner)], "replied", "unknown", text, partner)
            return

        bind_match = BIND_PATTERN.match(text)
        if bind_match:
            self._handle_bind(event, line_user_id, bind_match.group(1), text)
            return

        command = MENU_COMMANDS.get(text.replace(" ", ""))
        phone_match = PHONE_PATTERN.match(text)
        email_match = EMAIL_PATTERN.match(text)
        # 報修描述用房客原本打的字（不做全形轉半形，保留中文標點）。
        repair_match = REPAIR_PATTERN.match((message.get("text") or "").strip())
        if phone_match:
            command = "phone"
        elif email_match:
            command = "email"
        elif repair_match:
            command = "repair"

        if not command:
            # 看不懂的留言：目前沒有真人客服，回提示請房客用選單或找管家。
            self._reply_and_log(event, [self._build_unknown_message(partner)], "replied", "unknown", text, partner)
            return

        if not partner:
            self._reply_and_log(event, [self._build_unbound_message()], "unbound", command, text)
            return

        partner = partner.with_context(tz=partner.tz or "Asia/Taipei")
        if command == "repair":
            self._handle_repair(event, partner, repair_match.group(1).strip(), text)
            return
        if command == "bill":
            response = self._build_bill_message(partner)
        elif command == "bill_history":
            response = self._build_bill_history_message(partner)
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
            response = "⏳ 嘗試太多次了\n\n請 1 小時後再試，\n或向房間管家確認綁定碼"
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
            response = (
                "❌ 綁定碼不正確或已過期\n\n"
                "請確認 6 位數字打完整，\n"
                "傳「綁定碼 123456」\n或直接傳「123456」\n\n"
                "過期請向房間管家重新索取"
            )
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
        response = f"🎉 綁定完成！\n{partner.name} 您好\n\n" + self._build_help_message(partner)
        self._reply_and_log(event, [response], "bound", "bind", text, partner)

    @api.model
    def _handle_repair(self, event, partner, description, text):
        """「報修 馬桶壞掉」→ 在包租代管的報修單建立一張 [房客通報] 報修單。"""
        if not description:
            response = (
                "🔧 報修請這樣傳：\n\n"
                "報修 馬桶壞掉\n\n"
                f"再傳照片給我（最多 {REPAIR_PHOTO_LIMIT} 張）"
            )
            self._reply_and_log(event, [response], "replied", "repair", text, partner)
            return

        lease = self._get_partner_leases(partner)[:1]
        if not lease:
            response = "🔧 目前查不到您的租約\n\n請直接聯絡房間管家"
            self._reply_and_log(event, [response], "replied", "repair", text, partner)
            return

        manager = lease.property_id.manager_id
        request = self.env["ggandy.maintenance.request"].sudo().create(
            {
                "title": f"{REPAIR_TITLE_PREFIX}{description[:50]}",
                "description": (
                    f"<p>{html_escape(description)}</p><p>（房客透過 LINE 報修）</p>"
                ),
                "property_id": lease.property_id.id,
                "unit_id": lease.unit_id.id,
                "lease_id": lease.id,
                "tenant_id": partner.id,
                "reported_by_id": partner.id,
                # webhook 是公開使用者，負責人要明確指定（物件的管理人員）。
                "user_id": manager.id or False,
                "company_id": lease.company_id.id,
            }
        )
        if manager:
            request.activity_schedule(
                "mail.mail_activity_data_todo",
                summary="LINE 報修待處理",
                note=f"房客 {partner.name} 透過 LINE 報修：{description}",
                user_id=manager.id,
            )
        response = (
            "🔧 已收到報修\n\n"
            f"單號：{request.name}\n"
            f"{self._format_place(lease)}\n"
            f"問題：{description}\n\n"
            f"有照片請直接傳上來\n（最多 {REPAIR_PHOTO_LIMIT} 張）\n"
            "管家會盡快與您聯絡"
        )
        self._reply_and_log(event, [response], "replied", "repair", text, partner)

    @api.model
    def _handle_repair_photo(self, event, partner, message):
        """房客報修後傳的照片：附到 30 分鐘內那張 [房客通報] 報修單的 chatter，每張最多 3 張。"""
        text = "[image]"
        request = self.env["ggandy.maintenance.request"].sudo().search(
            [
                ("tenant_id", "=", partner.id),
                ("title", "=like", f"{REPAIR_TITLE_PREFIX}%"),
                ("state", "not in", ("done", "cancelled")),
                (
                    "create_date",
                    ">=",
                    fields.Datetime.now() - timedelta(minutes=REPAIR_PHOTO_WINDOW_MINUTES),
                ),
            ],
            order="create_date desc, id desc",
            limit=1,
        )
        if not request:
            response = (
                "📷 收到照片了\n\n"
                "要報修請先傳：\n報修 問題描述\n\n"
                "再傳照片給我"
            )
            self._reply_and_log(event, [response], "replied", "repair_photo", text, partner)
            return

        # 一次傳多張時 LINE 會拆成多個事件（imageSet.index 從 1 開始），只回第一張，避免洗版。
        image_set = message.get("imageSet") or {}
        first_of_set = int(image_set.get("index") or 1) == 1
        Attachment = self.env["ir.attachment"].sudo()
        photo_domain = [
            ("res_model", "=", request._name),
            ("res_id", "=", request.id),
            ("name", "=like", "LINE_%"),
        ]
        photo_count = Attachment.search_count(photo_domain)
        if photo_count >= REPAIR_PHOTO_LIMIT:
            if not image_set:
                response = (
                    f"📷 這張報修單已經有\n{REPAIR_PHOTO_LIMIT} 張照片了\n\n"
                    "其他照片請直接給管家"
                )
                self._reply_and_log(event, [response], "replied", "repair_photo", text, partner)
            else:
                self._create_log(event, "ignored", "repair_photo", text, f"超過 {REPAIR_PHOTO_LIMIT} 張，未存", partner)
            return

        try:
            content, content_type = LineAPIClient(
                self._get_parameter("channel_access_token")
            ).get_message_content(message.get("id"))
        except LineAPIError as error:
            _logger.warning("Unable to download LINE image: %s", error)
            self._create_log(event, "error", "repair_photo", text, f"下載照片失敗：{error}", partner)
            return

        extension = "png" if "png" in (content_type or "") else "jpg"
        attachment = Attachment.create(
            {
                "name": f"LINE_{request.name.replace('/', '-')}_{message.get('id')}.{extension}",
                "datas": base64.b64encode(content),
                "mimetype": content_type or "image/jpeg",
                "res_model": request._name,
                "res_id": request.id,
            }
        )
        request.message_post(body="房客透過 LINE 傳來的照片", attachment_ids=attachment.ids)

        if first_of_set:
            response = (
                "📷 照片已附在報修單\n"
                f"{request.name}\n\n"
                f"（每張報修單最多 {REPAIR_PHOTO_LIMIT} 張）"
            )
            self._reply_and_log(event, [response], "replied", "repair_photo", text, partner)
        else:
            self._create_log(event, "replied", "repair_photo", text, "（同一組照片，不另外回覆）", partner)

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
    def _format_place(self, record):
        """物件、房間各一行，避免名稱太長在手機上折行。"""
        return f"🏠 {record.property_id.name}\n房間：{record.unit_id.name}"

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
            return "📄 我的帳單\n\n目前沒有本月或\n未繳清的帳單"

        blocks = ["📄 我的帳單"]
        unpaid_total = 0.0
        shown_earlier = earlier[-EARLIER_UNPAID_LIMIT:]
        if len(earlier) > len(shown_earlier):
            blocks.append(
                f"※ 另有 {len(earlier) - len(shown_earlier)} 期更早的帳單\n未繳清，請洽房間管家"
            )
        for schedule in shown_earlier | current:
            paid = self._get_paid_amount(schedule)
            if schedule.collection_state != "paid":
                unpaid_total += max(schedule.total_amount - paid, 0.0)
            blocks.append(self._format_schedule(schedule, today, paid))

        currency = (current | earlier)[:1].currency_id
        if unpaid_total:
            blocks.append(
                f"{DIVIDER}\n💵 尚未繳清：{self._format_money(unpaid_total, currency)}\n"
                "匯款帳戶請按「繳費方式」"
            )
        else:
            blocks.append(f"{DIVIDER}\n🎉 帳單都繳清了，謝謝您！")
        return "\n\n".join(blocks)

    @api.model
    def _build_bill_history_message(self, partner):
        Schedule = self.env["ggandy.rent.schedule"].sudo()
        today = fields.Date.context_today(partner)
        next_month = today.replace(day=1) + relativedelta(months=1)
        schedules = Schedule.search(
            [("tenant_id", "=", partner.id), ("period_start", "<", next_month)]
            + Schedule._get_effective_domain(),
            order="period_start desc, id desc",
        ).filtered(lambda schedule: schedule.collection_state != "cancelled")[:BILL_HISTORY_LIMIT]
        if not schedules:
            return "🧾 帳單歷史\n\n目前沒有帳單紀錄"

        blocks = [f"🧾 最近 {BILL_HISTORY_LIMIT} 期帳單"]
        for schedule in schedules:
            blocks.append(self._format_schedule(schedule, today, self._get_paid_amount(schedule)))
        return "\n\n".join(blocks)

    @api.model
    def _format_schedule(self, schedule, today, paid):
        currency = schedule.currency_id
        money = lambda amount: self._format_money(amount, currency)  # noqa: E731
        lines = [
            DIVIDER,
            f"🗓 {schedule.period_month or ''} 帳單",
            f"（{schedule.period_start:%m/%d}～{schedule.period_end:%m/%d}）",
            self._format_place(schedule),
            "",
            f"租金：{money(schedule.rent_amount)}",
        ]
        if schedule.management_fee:
            lines.append(f"管理費：{money(schedule.management_fee)}")
        if schedule.electricity_amount or schedule.electricity_billing_type == "metered":
            lines.append(f"電費：{money(schedule.electricity_amount)}")
            if schedule.electricity_billing_type == "metered":
                lines.append(f"　（{schedule.electricity_usage:,.0f} 度）")
        if schedule.water_amount or schedule.water_billing_type == "metered":
            lines.append(f"水費：{money(schedule.water_amount)}")
            if schedule.water_billing_type == "metered":
                lines.append(f"　（{schedule.water_usage:,.0f} 度）")
        lines += [
            "",
            f"💵 合計：{money(schedule.total_amount)}",
        ]
        if schedule.collection_state == "partial":
            lines.append(f"已繳：{money(paid)}")
            lines.append(f"尚欠：{money(max(schedule.total_amount - paid, 0.0))}")
        lines.append(f"繳租日：{schedule.due_date:%m/%d}")
        lines.append(self._get_tenant_status(schedule, today))
        if schedule.meter_reading_pending:
            lines.append("※ 水電待抄表，金額可能調整")
        return "\n".join(lines)

    @api.model
    def _get_tenant_status(self, schedule, today):
        """房客看的狀態：草稿帳單、未開單對房客來說都是「待繳」。"""
        if schedule.collection_state == "paid":
            return "✅ 已繳清"
        overdue = schedule.due_date and schedule.due_date < today
        if schedule.collection_state == "partial":
            return "⚠️ 部分已繳（已逾期）" if overdue else "🟡 部分已繳"
        return "⚠️ 已逾期，請盡快繳納" if overdue else "⏳ 待繳"

    @api.model
    def _build_payment_message(self, partner):
        leases = self._get_partner_leases(partner)
        if not leases:
            return "💳 繳費方式\n\n目前沒有生效中的租約\n請洽房間管家"

        report = self.env["report.ggandy_property_management.report_lease_contract"].sudo()
        blocks = ["💳 繳費方式"]
        for lease in leases:
            parties = report._get_contract_parties(lease)
            bank = parties["bank"]
            payee = parties["agent"] or parties["lessor"]
            lines = [DIVIDER, self._format_place(lease), ""]
            if bank:
                if bank.bank_id:
                    lines.append(f"銀行：{bank.bank_id.name}")
                lines.append(f"帳號：{bank.acc_number}")
                lines.append(f"戶名：{bank.acc_holder_name or payee.name}")
            else:
                lines.append("繳款帳戶請洽房間管家")
            lines.append(f"繳租日：每月 {lease.rent_due_day} 日")
            blocks.append("\n".join(lines))
        blocks.append(f"{DIVIDER}\n轉帳時請在備註寫上房號 🙏")
        return "\n\n".join(blocks)

    @api.model
    def _build_lease_message(self, partner):
        leases = self._get_partner_leases(partner)
        if not leases:
            return "📋 我的租約\n\n目前沒有生效中的租約\n請洽房間管家"

        today = fields.Date.context_today(partner)
        blocks = ["📋 我的租約"]
        for lease in leases:
            money = lambda amount: self._format_money(amount, lease.currency_id)  # noqa: E731
            if lease.state == "expired" or lease.end_date < today:
                remaining = "⚠️ 已到期，續約請洽管家"
            else:
                remaining = f"⏳ 還有 {(lease.end_date - today).days} 天到期"
            lines = [
                DIVIDER,
                self._format_place(lease),
                "",
                f"起租：{lease.start_date:%Y/%m/%d}",
                f"到期：{lease.end_date:%Y/%m/%d}",
                remaining,
                "",
                f"月租：{money(lease.rent_amount)}",
            ]
            if lease.management_fee:
                lines.append(f"管理費：{money(lease.management_fee)}")
            lines += [
                f"押金：{money(lease.deposit_amount)}",
                f"繳租日：每月 {lease.rent_due_day} 日",
            ]
            blocks.append("\n".join(lines))
        return "\n\n".join(blocks)

    @api.model
    def _build_profile_message(self, partner):
        return (
            "✏️ 我的聯絡資料\n\n"
            f"姓名：{partner.name}\n"
            f"電話：{partner.phone or '（未填）'}\n"
            f"Email：{partner.email or '（未填）'}\n\n"
            f"{DIVIDER}\n"
            "要修改請直接傳 👇\n\n"
            "電話 0912345678\n"
            "email abc@gmail.com\n\n"
            "（姓名等其他資料\n請洽房間管家）"
        )

    @api.model
    def _update_phone(self, partner, value):
        phone = re.sub(r"[\s\-()（）]", "", value)
        if not (
            re.fullmatch(r"09\d{8}", phone)
            or re.fullmatch(r"0[2-8]\d{7,8}", phone)
            or re.fullmatch(r"\+886\d{8,9}", phone)
        ):
            return "❌ 電話格式不對\n\n請再傳一次，例如：\n電話 0912345678"
        old_phone = partner.phone or "（未填）"
        partner.write({"phone": phone})
        partner.message_post(body=f"房客透過 LINE 將電話由 {old_phone} 改為 {phone}。")
        return f"✅ 電話已更新\n\n{phone}"

    @api.model
    def _update_email(self, partner, value):
        email = email_normalize(value.strip())
        if not email:
            return "❌ Email 格式不對\n\n請再傳一次，例如：\nemail abc@gmail.com"
        old_email = partner.email or "（未填）"
        partner.write({"email": email})
        partner.message_post(body=f"房客透過 LINE 將 Email 由 {old_email} 改為 {email}。")
        return f"✅ Email 已更新\n\n{email}"

    @api.model
    def _get_manager_lines(self, partner):
        """房客各物件的房間管家（物件的管理人員）與電話。"""
        blocks = []
        for lease in self._get_partner_leases(partner):
            manager = lease.property_id.manager_id
            if not manager:
                continue
            lines = [f"🏠 {lease.property_id.name}", f"管家：{manager.name}"]
            if manager.partner_id.phone:
                lines.append(f"電話：{manager.partner_id.phone}")
            block = "\n".join(lines)
            if block not in blocks:
                blocks.append(block)
        return blocks

    @api.model
    def _build_contact_message(self, partner):
        blocks = [
            "🙋 聯絡房間管家",
            "這裡是自動回覆小幫手\n沒有真人客服喔！",
            "房間有任何狀況\n（報修、退租、續約…）\n請直接聯絡管家：",
        ]
        managers = self._get_manager_lines(partner)
        if managers:
            blocks.append(f"{DIVIDER}\n" + "\n\n".join(managers))
        else:
            company = self._get_partner_company(partner)
            blocks.append(
                f"{DIVIDER}\n電話：{company.phone}" if company.phone else f"{DIVIDER}\n請聯絡您的房間管家"
            )
        return "\n\n".join(blocks)

    @api.model
    def _build_unbound_message(self):
        return (
            "🔒 還沒綁定喔\n\n"
            "請向房間管家索取\n"
            "6 位數綁定碼，再傳：\n\n"
            "綁定碼 123456\n"
            "（或直接傳 123456）"
        )

    @api.model
    def _build_unknown_message(self, partner):
        lines = [
            "🤖 我是自動回覆小幫手",
            "看不懂這則訊息",
            "",
            "請點下方選單查詢",
        ]
        if partner:
            lines += [
                "",
                "要報修請傳：",
                "報修 問題描述",
                "",
                "其他房間的事請聯絡管家",
                "（按「聯絡管理員」）",
            ]
        else:
            lines += ["", "第一次使用請先傳：", "綁定碼 123456", "（或直接傳 123456）"]
        return "\n".join(lines)

    @api.model
    def _build_help_message(self, partner):
        company = self._get_partner_company(partner)
        return (
            f"{company.name}\n"
            "房客小幫手可以幫您：\n\n"
            "📄 帳單｜本月與未繳帳單\n"
            "💳 繳費｜匯款帳戶\n"
            "📋 租約｜租期與押金\n"
            "✏️ 修改資料｜電話、Email\n"
            "🙋 聯絡管理員｜找管家\n\n"
            "也可以直接打字：\n"
            "🧾 帳單歷史｜最近 3 期\n"
            "🔧 報修 馬桶壞掉\n"
            f"　（再傳照片，最多 {REPAIR_PHOTO_LIMIT} 張）\n\n"
            "點下方選單就能查詢"
        )
