from django.contrib import admin

from .models import Customer


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ("display_name", "line_user_id", "phone", "is_line_friend", "created_at")
    list_filter = ("is_line_friend",)
    search_fields = ("display_name", "line_user_id", "phone")
    readonly_fields = ("line_user_id", "is_line_friend", "created_at", "updated_at")
