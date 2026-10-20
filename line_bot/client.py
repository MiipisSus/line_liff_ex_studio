"""Messaging API 的薄包裝。

計費：reply 不計費；push 計入每月訊息額度。每個呼叫點都要標明用的是哪一種，
方便估算月用量（見 CLAUDE.md「通知策略」）。
"""

import logging
import uuid

import requests
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

logger = logging.getLogger(__name__)

API_BASE = "https://api.line.me/v2/bot"
DATA_API_BASE = "https://api-data.line.me/v2/bot"


class LineMessagingError(Exception):
    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


def _headers(extra=None):
    token = settings.LINE_MESSAGING_CHANNEL_ACCESS_TOKEN
    if not token:
        raise ImproperlyConfigured("未設定 LINE_MESSAGING_CHANNEL_ACCESS_TOKEN")
    return {"Authorization": f"Bearer {token}", **(extra or {})}


def request(method, path, *, base=API_BASE, json=None, data=None, headers=None):
    try:
        resp = requests.request(
            method, f"{base}{path}", json=json, data=data, headers=_headers(headers), timeout=10
        )
    except requests.RequestException as exc:
        raise LineMessagingError(f"無法連線到 LINE：{exc}") from exc
    if resp.status_code >= 400:
        raise LineMessagingError(f"LINE API {method} {path} 回傳 {resp.status_code}：{resp.text}", resp.status_code)
    return resp.json() if resp.content else {}


def get_profile(user_id):
    """【不計費】取得使用者資料。使用者未加好友或已封鎖時 LINE 回 404。"""
    return request("GET", f"/profile/{user_id}")


def reply_message(reply_token, messages):
    """【reply・不計費】回覆 webhook 事件。reply token 只能用一次，且有時效。"""
    return request("POST", "/message/reply", json={"replyToken": reply_token, "messages": messages})


def push_message(to, messages, retry_key=None):
    """【push・計費】主動推播。retry_key 相同的重送不會重複發送（LINE 端去重）。"""
    headers = {"X-Line-Retry-Key": retry_key or str(uuid.uuid4())}
    return request("POST", "/message/push", json={"to": to, "messages": messages}, headers=headers)
