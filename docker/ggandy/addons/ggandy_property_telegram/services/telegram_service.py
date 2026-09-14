import requests


class TelegramAPIError(Exception):
    """Raised when Telegram returns an HTTP or Bot API error."""


class TelegramAPIClient:
    API_BASE_URL = "https://api.telegram.org"

    def __init__(self, token, timeout=20):
        if not token:
            raise TelegramAPIError("尚未設定 Telegram Bot Token。")
        self._base_url = f"{self.API_BASE_URL}/bot{token}"
        self._timeout = timeout

    def call(self, method, payload=None):
        try:
            response = requests.post(
                f"{self._base_url}/{method}",
                json=payload or {},
                timeout=self._timeout,
            )
            response.raise_for_status()
            result = response.json()
        except (requests.RequestException, ValueError) as error:
            raise TelegramAPIError(f"Telegram API 連線失敗：{error}") from error

        if not result.get("ok"):
            raise TelegramAPIError(
                result.get("description") or f"Telegram API {method} 執行失敗。"
            )
        return result.get("result")

    def get_me(self):
        return self.call("getMe")

    def get_updates(self, offset=None, limit=100):
        payload = {
            "limit": limit,
            "timeout": 0,
            "allowed_updates": ["message"],
        }
        if offset is not None:
            payload["offset"] = offset
        return self.call("getUpdates", payload)

    def send_message(self, chat_id, text):
        return self.call(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": text,
            },
        )

