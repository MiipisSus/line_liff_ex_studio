from django.contrib import admin, messages
from django.contrib.admin import helpers
from django.template.response import TemplateResponse

from . import services
from .models import SHOP_TZ, Booking, ScheduleException, Service, Shop, Staff, WorkingHours


def warn_bookings_outside_schedule(request, staff_ids=None, date=None):
    """班表 / 例外日異動後，提示已不在上班時間內的預約。"""
    affected = services.find_bookings_outside_schedule(staff_ids=staff_ids, date_from=date, date_to=date)
    if not affected:
        return
    lines = [
        f"{b.start_at.astimezone(SHOP_TZ):%m/%d %H:%M} {b.staff}・{b.customer}・{b.service}（{b.get_status_display()}）"
        for b in affected[:10]
    ]
    more = f"⋯另有 {len(affected) - 10} 筆" if len(affected) > 10 else ""
    messages.warning(
        request,
        f"有 {len(affected)} 筆預約已不在人員的上班時間內，請聯繫顧客改期或到「預約」取消："
        + "；".join(lines) + more,
    )


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

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        if change:
            warn_bookings_outside_schedule(request, staff_ids=[form.instance.pk])


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

    def _warn(self, request, obj):
        warn_bookings_outside_schedule(request, staff_ids=[obj.staff_id] if obj.staff_id else None, date=obj.date)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        self._warn(request, obj)

    def delete_model(self, request, obj):
        # 刪除「特別上班」也可能讓既有預約落在班表外
        super().delete_model(request, obj)
        self._warn(request, obj)


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    """預約不能直接編輯：建立與狀態轉換一律透過 booking.services，用下方的動作操作。"""

    list_display = ("start_at", "customer", "service", "staff", "price", "status", "cancelled_by")
    list_filter = ("status", "staff")
    date_hierarchy = "start_at"
    actions = ["confirm", "complete", "mark_no_show", "cancel_by_shop"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def _apply(self, request, queryset, func, verb):
        done, errors = 0, []
        for booking in queryset:
            try:
                func(booking)
                done += 1
            except services.BookingError as exc:
                errors.append(f"{booking}：{exc}")
        if done:
            self.message_user(request, f"已{verb} {done} 筆預約", messages.SUCCESS)
        for error in errors:
            self.message_user(request, error, messages.ERROR)

    @admin.action(description="確認預約（推播通知顧客）")
    def confirm(self, request, queryset):
        self._apply(request, queryset, services.confirm_booking, "確認")

    @admin.action(description="標記為已完成")
    def complete(self, request, queryset):
        self._apply(request, queryset, services.complete_booking, "完成")

    @admin.action(description="標記為未到（no-show）")
    def mark_no_show(self, request, queryset):
        self._apply(request, queryset, services.mark_no_show, "標記未到")

    @admin.action(description="店家取消（推播通知顧客）")
    def cancel_by_shop(self, request, queryset):
        # 取消會推播給顧客，先顯示確認頁
        if request.POST.get("confirm") == "yes":
            self._apply(request, queryset, services.cancel_booking_by_shop, "取消")
            return None
        return TemplateResponse(request, "admin/booking/booking/cancel_confirmation.html", {
            **self.admin_site.each_context(request),
            "title": "確定要取消這些預約嗎？",
            "opts": self.model._meta,
            "bookings": queryset.select_related("customer", "service", "staff"),
            "action_checkbox_name": helpers.ACTION_CHECKBOX_NAME,
        })
