"""店家端操作觸發的推播通知。全部是【push・計費】，只在必要時使用。

呼叫時機：booking.services 在交易 commit 後呼叫（transaction.on_commit）。
發送失敗只記錄 log，不影響已完成的狀態變更。里程碑 5 導入 django-q2 後改為背景任務並重試。
"""

import logging
import uuid

from django.conf import settings

from . import client, messages

logger = logging.getLogger(__name__)


def _push_booking(booking, title):
    if not settings.LINE_MESSAGING_CHANNEL_ACCESS_TOKEN:
        logger.warning("未設定 Messaging API token，略過通知 booking=%s", booking.pk)
        return
    try:
        # 【push・計費】
        client.push_message(
            booking.customer.line_user_id,
            [messages.booking_detail(booking, title)],
            # 同一筆預約、同一狀態只會通知一次；重送時 LINE 依 retry key 去重（24 小時內有效）
            retry_key=str(uuid.uuid5(uuid.NAMESPACE_URL, f"booking:{booking.pk}:{booking.status}")),
        )
    except client.LineMessagingError:
        logger.exception("推播失敗 booking=%s title=%s", booking.pk, title)


def notify_booking_confirmed(booking):
    """【push・計費】店家確認預約。"""
    _push_booking(booking, "預約已確認")


def notify_booking_cancelled_by_shop(booking):
    """【push・計費】店家取消預約。"""
    _push_booking(booking, "預約已被店家取消")
