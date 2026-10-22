"""一次執行所有排程工作後結束。

給沒有常駐 qcluster 的環境用（Fly.io 的 scheduled machine 每小時喚醒一台機器執行本指令）。
有常駐 qcluster 時改用 setup_schedules 建立 django-q2 排程，不需要本指令。
"""

import logging

from django.core.management.base import BaseCommand, CommandError

from booking import tasks
from line_bot import notifications

logger = logging.getLogger(__name__)

JOBS = [
    ("未確認自動取消", tasks.cancel_unconfirmed_bookings),
    ("預約提醒", tasks.send_due_reminders),
    ("重試失敗的推播", notifications.retry_failed_notifications),
]


class Command(BaseCommand):
    help = "執行一次所有排程工作（自動取消、提醒、重試推播）後結束"

    def handle(self, *args, **options):
        failed = []
        for name, job in JOBS:
            # 單一工作失敗不影響其他工作；全部跑完後以非零結束碼回報
            try:
                result = job()
                self.stdout.write(f"{name}：{result}")
            except Exception:
                logger.exception("排程工作失敗：%s", name)
                failed.append(name)
        if failed:
            raise CommandError(f"以下工作失敗：{'、'.join(failed)}")
