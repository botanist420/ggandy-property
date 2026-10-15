import base64
import hashlib
import hmac

import requests


class LineAPIError(Exception):
    """Raised when LINE returns an HTTP or Messaging API error."""


# LINE 單則文字訊息上限 5,000 字。
MAX_TEXT_LENGTH = 5000


def verify_signature(channel_secret, body, signature):
    """驗證 webhook 的 X-Line-Signature：HMAC-SHA256(channel secret, 原始 body) 再 base64。"""
    if not channel_secret or not signature:
        return False
    digest = hmac.new(channel_secret.encode("utf-8"), body, hashlib.sha256).digest()
    return hmac.compare_digest(base64.b64encode(digest).decode("utf-8"), signature)


class LineAPIClient:
    API_BASE_URL = "https://api.line.me/v2/bot"
    # 下載房客傳來的圖片等內容要用另一個網域。
    DATA_API_BASE_URL = "https://api-data.line.me/v2/bot"

    def __init__(self, token, timeout=10):
        if not token:
            raise LineAPIError("尚未設定 LINE Channel access token。")
        self._headers = {"Authorization": f"Bearer {token}"}
        self._timeout = timeout

    def _request(self, method, path, payload=None):
        try:
            response = requests.request(
                method,
                f"{self.API_BASE_URL}{path}",
                headers=self._headers,
                json=payload,
                timeout=self._timeout,
            )
        except requests.RequestException as error:
            raise LineAPIError(f"LINE API 連線失敗：{error}") from error

        if response.status_code >= 400:
            try:
                message = response.json().get("message")
            except ValueError:
                message = response.text
            raise LineAPIError(f"LINE API 錯誤（HTTP {response.status_code}）：{message}")
        try:
            return response.json()
        except ValueError:
            return {}

    def get_bot_info(self):
        return self._request("GET", "/info")

    def get_profile(self, user_id):
        return self._request("GET", f"/profile/{user_id}")

    def get_message_content(self, message_id):
        """下載房客傳來的圖片，回傳 (bytes, content-type)。"""
        try:
            response = requests.get(
                f"{self.DATA_API_BASE_URL}/message/{message_id}/content",
                headers=self._headers,
                timeout=20,
            )
        except requests.RequestException as error:
            raise LineAPIError(f"LINE API 連線失敗：{error}") from error
        if response.status_code >= 400:
            raise LineAPIError(f"LINE API 錯誤（HTTP {response.status_code}）：無法下載內容")
        return response.content, response.headers.get("Content-Type", "image/jpeg")

    def reply_message(self, reply_token, messages):
        """用 Reply API 回覆（不計入訊息則數）。一次最多 5 則；字串轉成文字訊息，dict（Flex、帶快速回覆）原樣送出。"""
        payload_messages = []
        for message in messages[:5]:
            if isinstance(message, str):
                message = {"type": "text", "text": message}
            if message.get("type") == "text" and len(message["text"]) > MAX_TEXT_LENGTH:
                message = dict(message, text=message["text"][: MAX_TEXT_LENGTH - 30] + "\n…（內容太長，請洽管理員）")
            payload_messages.append(message)
        messages = payload_messages
        return self._request(
            "POST",
            "/message/reply",
            {"replyToken": reply_token, "messages": messages},
        )
