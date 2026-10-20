"""Messaging API webhook 的事件處理。這裡的回覆全部是【reply・不計費】。"""

import base64
import hashlib
import hmac
import logging
import re
from urllib.parse import parse_qs

from django.conf import settings

from booking import services as booking_services
from liff.models import Customer

from . import client, messages

logger = logging.getLogger(__name__)

# LIFF 預約成功後以 liff.sendMessages() 代替顧客送出的文字，見 liff/templates/liff/index.html
BOOKING_CREATED_PATTERN = re.compile(r"^我已完成預約\s*#(\d+)$")
MY_BOOKINGS_KEYWORD = "我的預約"
WELCOME_TEXT = "感謝加入！點選下方選單的「我要預約」即可線上預約，「我的預約」可查詢或取消預約。"


def verify_signature(body: bytes, signature: str) -> bool:
    secret = settings.LINE_MESSAGING_CHANNEL_SECRET
    if not secret or not signature:
        return False
    digest = hmac.new(secret.encode(), body, hashlib.sha256).digest()
    return hmac.compare_digest(base64.b64encode(digest).decode(), signature)


def handle_events(payload):
    for event in payload.get("events", []):
        try:
            handle_event(event)
        except Exception:
            # 單一事件失敗不影響其他事件，也不讓 LINE 重送整批
            logger.exception("處理 webhook 事件失敗：%s", event.get("webhookEventId"))


def refresh_friendship(customer):
    """查詢顧客是否為官方帳號好友並記錄，回傳 True / False / None（無法判斷）。

    無法判斷時（未設定 token、LINE 連線失敗）保留原本的值，交由呼叫端決定是否提醒。
    """
    if not settings.LINE_MESSAGING_CHANNEL_ACCESS_TOKEN:
        return None
    try:
        client.get_profile(customer.line_user_id)
        is_friend = True
    except client.LineMessagingError as exc:
        if exc.status_code != 404:
            logger.warning("查詢好友狀態失敗 customer=%s：%s", customer.pk, exc)
            return None
        # 404：未加好友或已封鎖
        is_friend = False
    if customer.is_line_friend != is_friend:
        customer.is_line_friend = is_friend
        customer.save(update_fields=["is_line_friend", "updated_at"])
    return is_friend


def handle_event(event):
    source = event.get("source", {})
    event_type = event.get("type")
    # 只處理 1 對 1 聊天；webhook 已驗簽，這裡的 userId 可信
    if source.get("type") != "user":
        return
    customer = Customer.objects.filter(line_user_id=source.get("userId")).first()

    # 好友狀態：unfollow（封鎖）沒有 replyToken，要在檢查 replyToken 之前處理
    if customer and event_type in ("follow", "unfollow"):
        Customer.objects.filter(pk=customer.pk).update(is_line_friend=event_type == "follow")

    reply_token = event.get("replyToken")
    if not reply_token:
        return

    reply = None
    if event_type == "follow":
        reply = [messages.text(WELCOME_TEXT)]
    elif event_type == "message" and event["message"].get("type") == "text":
        reply = _handle_text(event["message"]["text"].strip(), customer)
    elif event_type == "postback":
        params = {k: v[0] for k, v in parse_qs(event["postback"].get("data", "")).items()}
        reply = _handle_postback(params, customer)

    if reply:
        # 【reply・不計費】
        client.reply_message(reply_token, reply)


def _my_bookings(customer):
    bookings = booking_services.get_upcoming_bookings(customer) if customer else []
    return [messages.booking_list(bookings)]


def _handle_text(text, customer):
    if text == MY_BOOKINGS_KEYWORD:
        return _my_bookings(customer)

    match = BOOKING_CREATED_PATTERN.match(text)
    if match and customer:
        booking = customer.bookings.select_related("service", "staff").filter(pk=match.group(1)).first()
        if booking:
            return [messages.booking_detail(booking, "預約已送出，待店家確認")]

    # 其他文字不回覆，保留給店家在官方帳號後台手動回覆
    return None


def _get_customer_booking(customer, booking_id):
    if not customer or not str(booking_id).isdigit():
        return None
    return customer.bookings.select_related("service", "staff").filter(pk=booking_id).first()


def _handle_postback(params, customer):
    action = params.get("action")

    if action == "my_bookings":
        return _my_bookings(customer)

    if action == "cancel_request":
        booking = _get_customer_booking(customer, params.get("booking"))
        if booking is None:
            return [messages.text("找不到這筆預約")]
        try:
            booking_services.ensure_customer_can_cancel(booking)
        except booking_services.BookingError as exc:
            return [messages.text(str(exc))]
        return [messages.cancel_confirm(booking)]

    if action == "cancel_confirm":
        if customer is None or not str(params.get("booking", "")).isdigit():
            return [messages.text("找不到這筆預約")]
        try:
            booking = booking_services.cancel_booking_by_customer(int(params["booking"]), customer)
        except booking_services.BookingError as exc:
            return [messages.text(str(exc))]
        return [messages.booking_detail(booking, "預約已取消")]

    if action == "cancel_keep":
        return [messages.text("好的，已為您保留預約")]

    return None
