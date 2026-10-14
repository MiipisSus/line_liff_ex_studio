from django.db import models


class Customer(models.Model):
    """LINE 使用者。line_user_id 只能來自後端驗證過的 ID token（sub）。"""

    line_user_id = models.CharField("LINE userId", max_length=64, unique=True)
    display_name = models.CharField("顯示名稱", max_length=255, blank=True)
    phone = models.CharField("電話", max_length=32, blank=True)
    created_at = models.DateTimeField("建立時間", auto_now_add=True)
    updated_at = models.DateTimeField("更新時間", auto_now=True)

    class Meta:
        verbose_name = "顧客"
        verbose_name_plural = "顧客"

    def __str__(self):
        return self.display_name or self.line_user_id
