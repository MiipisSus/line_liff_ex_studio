"""推播通知。全部是【push・計費】，只在必要時使用。

由 booking.services 在交易 commit 後呼叫 enqueue_booking_notification()：
- 有常駐 qcluster：排入 django-q2 背景任務，失敗丟例外，由 django-q2 重試（Q_CLUSTER 的 retry / max_attempts）。
- TASKS_SYNC=True（Fly.io，沒有常駐 qcluster）：當下直接發送；失敗不丟給呼叫端（不讓後台操作變成 500），
  記錄到 FailedNotification，由 run_scheduled_jobs 每小時重試。
  （django-q2 的 sync 模式會把例外直接丟回呼叫端，也不寫 Failure，所以不用它。）
X-Line-Retry-Key 由「預約 ID + 通知種類」產生，重試不會讓顧客重複收到。
"""

import logging
import uuid
from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from django_q.conf import Conf
from django_q.tasks import async_task

from booking.models import Booking

from . import client, messages
from .models import FailedNotification

logger = logging.getLogger(__name__)

# 通知種類 → (標題, 發送時預約必須處於的狀態)
# 發送前會重新讀取預約：狀態已改變（例如確認後又被取消）就不再發送過時的通知
KINDS = {
    "confirmed": ("預約已確認", Booking.Status.CONFIRMED),
    "cancelled_by_shop": ("預約已被店家取消", Booking.Status.CANCELLED),
    "auto_cancelled": ("預約未獲確認，已自動取消", Booking.Status.CANCELLED),
    "reminder": ("預約提醒", Booking.Status.CONFIRMED),
}


# LINE 依 X-Line-Retry-Key 去重的有效期；超過就不再重試，以免顧客重複收到
RETRY_WINDOW = timedelta(hours=24)


def enqueue_booking_notification(booking_id, kind):
    if kind not in KINDS:
        raise ValueError(f"未知的通知種類：{kind}")
    if not Conf.SYNC:
        async_task("line_bot.notifications.send_booking_notification", booking_id, kind)
        return
    try:
        send_booking_notification(booking_id, kind)
    except Exception as exc:
        logger.exception("推播失敗，稍後由排程重試 booking=%s kind=%s", booking_id, kind)
        FailedNotification.objects.update_or_create(
            booking_id=booking_id, kind=kind, defaults={"last_error": str(exc)}
        )


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


def retry_failed_notifications():
    """重試 FailedNotification 中 24 小時內的推播，回傳處理完成（送出或已不需送出）的筆數。

    成功或因狀態已變更而略過就刪除紀錄；仍失敗則累加次數，下次再試；超過 24 小時的紀錄放棄並刪除。
    """
    cutoff = timezone.now() - RETRY_WINDOW
    expired, _ = FailedNotification.objects.filter(created_at__lt=cutoff).delete()
    if expired:
        logger.warning("放棄 %s 則超過 24 小時仍未送出的推播", expired)

    done = 0
    for failed in FailedNotification.objects.all():
        try:
            send_booking_notification(failed.booking_id, failed.kind)
        except Exception as exc:
            logger.exception("重試推播仍失敗 booking=%s kind=%s", failed.booking_id, failed.kind)
            failed.attempts += 1
            failed.last_error = str(exc)
            failed.save(update_fields=["attempts", "last_error", "updated_at"])
            continue
        failed.delete()
        done += 1
    return done
