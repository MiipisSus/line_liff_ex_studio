"""django-q2 排程進入點：只做薄包裝，邏輯在 booking/services.py。

排程由 `python manage.py setup_schedules` 建立。
"""

import logging

from . import services

logger = logging.getLogger(__name__)


def send_due_reminders():
    count = services.send_due_reminders()
    if count:
        logger.info("已排入 %s 則預約提醒", count)
    return count


def cancel_unconfirmed_bookings():
    count = services.cancel_unconfirmed_bookings()
    if count:
        logger.info("已自動取消 %s 筆未確認預約", count)
    return count
