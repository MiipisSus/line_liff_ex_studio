from django.contrib import admin

from .models import FailedNotification


@admin.register(FailedNotification)
class FailedNotificationAdmin(admin.ModelAdmin):
    """唯讀：重試由排程處理，這裡只供查看。"""

    list_display = ("booking_id", "kind", "attempts", "created_at", "updated_at")
    list_filter = ("kind",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
