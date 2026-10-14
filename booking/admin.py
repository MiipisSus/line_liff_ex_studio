from django.contrib import admin

from .models import Booking, ScheduleException, Service, Shop, Staff, WorkingHours


@admin.register(Shop)
class ShopAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return not Shop.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


class WorkingHoursInline(admin.TabularInline):
    model = WorkingHours
    extra = 0


@admin.register(Staff)
class StaffAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active", "sort_order")
    list_editable = ("is_active", "sort_order")
    inlines = [WorkingHoursInline]


@admin.register(Service)
class ServiceAdmin(admin.ModelAdmin):
    list_display = ("name", "duration_minutes", "price", "is_active", "sort_order")
    list_editable = ("is_active", "sort_order")
    filter_horizontal = ("staff",)


@admin.register(ScheduleException)
class ScheduleExceptionAdmin(admin.ModelAdmin):
    list_display = ("date", "staff", "kind", "start_time", "end_time", "note")
    list_filter = ("kind", "staff")
    date_hierarchy = "date"


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    """目前唯讀：建立預約與狀態轉換要經過 service layer（里程碑 3、4）。"""

    list_display = ("start_at", "customer", "service", "staff", "status")
    list_filter = ("status", "staff")
    date_hierarchy = "start_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
