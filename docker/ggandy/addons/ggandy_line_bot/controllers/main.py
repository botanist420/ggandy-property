import json
import logging

from odoo import http
from odoo.http import request

from ..services import verify_signature


_logger = logging.getLogger(__name__)


class GgandyLineBotController(http.Controller):
    @http.route(
        "/ggandy/line/webhook",
        type="http",
        auth="public",
        methods=["POST"],
        csrf=False,
        save_session=False,
    )
    def line_webhook(self, **kwargs):
        body = request.httprequest.get_data()
        signature = request.httprequest.headers.get("X-Line-Signature", "")
        Log = request.env["ggandy.line.bot.log"].sudo()

        # 沒有登入使用者，只能靠簽章確認請求真的來自 LINE。
        if not verify_signature(Log._get_parameter("channel_secret"), body, signature):
            _logger.warning("GGAndy LINE webhook rejected: invalid signature.")
            return request.make_response("invalid signature", status=400)

        try:
            payload = json.loads(body or b"{}")
        except ValueError:
            return request.make_response("invalid body", status=400)

        # Console 的「Verify」會送空的 events，直接回 200。
        for event in payload.get("events") or []:
            try:
                with request.env.cr.savepoint():
                    Log._process_event(event)
            except Exception:
                _logger.exception("Unable to process LINE event %s.", event.get("webhookEventId"))
                Log._log_error(event, "處理 LINE 事件時發生未預期錯誤。")
        return request.make_response("OK", status=200)
