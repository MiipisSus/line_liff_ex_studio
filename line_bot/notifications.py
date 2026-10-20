"""推播通知。全部是【push・計費】，只在必要時使用。

由 booking.services 在交易 commit 後以 django-q2 背景任務排入（enqueue_booking_notification），
不在請求內同步發送。發送失敗會丟出例外，交給 django-q2 重試（Q_CLUSTER 的 retry / max_attempts）；
X-Line-Retry-Key 由「預約 ID + 通知種類」產生，重試不會讓顧客重複收到。
"""

import logging
import uuid

from django.conf import settings
from django_q.tasks import async_task

from booking.models import Booking

from . import client, messages

logger = logging.getLogger(__name__)

# 通知種類 → (標題, 發送時預約必須處於的狀態)
# 發送前會重新讀取預約：狀態已改變（例如確認後又被取消）就不再發送過時的通知
KINDS = {
    "confirmed": ("預約已確認", Booking.Status.CONFIRMED),
    "cancelled_by_shop": ("預約已被店家取消", Booking.Status.CANCELLED),
    "auto_cancelled": ("預約未獲確認，已自動取消", Booking.Status.CANCELLED),
    "reminder": ("預約提醒", Booking.Status.CONFIRMED),
}


def enqueue_booking_notification(booking_id, kind):
    if kind not in KINDS:
        raise ValueError(f"未知的通知種類：{kind}")
    async_task("line_bot.notifications.send_booking_notification", booking_id, kind)


def send_booking_notification(booking_id, kind):
    """【push・計費】背景任務：發送一則預約相關推播。失敗時丟出例外以觸發重試。"""
    title, expected_status = KINDS[kind]
    if not settings.LINE_MESSAGING_CHANNEL_ACCESS_TOKEN:
        logger.warning("未設定 Messaging API token，略過通知 booking=%s kind=%s", booking_id, kind)
        return "skipped: no token"

    booking = Booking.objects.select_related("customer", "service", "staff").filter(pk=booking_id).first()
    if booking is None or booking.status != expected_status:
        return "skipped: stale"
    if booking.customer.is_line_friend is False:
        # 已知不是好友（或已封鎖），推播送不到，不浪費額度
        return "skipped: not friend"

    client.push_message(
        booking.customer.line_user_id,
        [messages.booking_detail(booking, title)],
        retry_key=str(uuid.uuid5(uuid.NAMESPACE_URL, f"booking:{booking.pk}:{kind}")),
    )
    return "sent"
