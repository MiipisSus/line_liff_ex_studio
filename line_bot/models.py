from django.db import models


class FailedNotification(models.Model):
    """同步模式（TASKS_SYNC=True，沒有常駐 qcluster）下發送失敗的推播，由 run_scheduled_jobs 重試。

    有常駐 qcluster 時失敗由 django-q2 自己重試，不會寫入這張表。
    """

    booking_id = models.PositiveIntegerField("預約 ID")
    kind = models.CharField("通知種類", max_length=30)
    attempts = models.PositiveSmallIntegerField("嘗試次數", default=1)
    last_error = models.TextField("最後錯誤", blank=True)
    created_at = models.DateTimeField("第一次失敗時間", auto_now_add=True)
    updated_at = models.DateTimeField("最後嘗試時間", auto_now=True)

    class Meta:
        verbose_name = "發送失敗的推播"
        verbose_name_plural = "發送失敗的推播"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["booking_id", "kind"], name="failed_notification_unique"),
        ]

    def __str__(self):
        return f"預約 #{self.booking_id} {self.kind}"
