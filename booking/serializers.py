from rest_framework import serializers

from .models import SHOP_TZ, Service, Staff


class StaffSerializer(serializers.ModelSerializer):
    class Meta:
        model = Staff
        fields = ["id", "name"]


class ServiceSerializer(serializers.ModelSerializer):
    staff = serializers.SerializerMethodField()

    class Meta:
        model = Service
        fields = ["id", "name", "duration_minutes", "price", "staff"]

    def get_staff(self, obj):
        # 只列出啟用中的人員；queryset 已 prefetch
        return StaffSerializer([s for s in obj.staff.all() if s.is_active], many=True).data


class AvailabilityQuerySerializer(serializers.Serializer):
    service = serializers.PrimaryKeyRelatedField(queryset=Service.objects.filter(is_active=True))
    date = serializers.DateField()
    staff = serializers.PrimaryKeyRelatedField(queryset=Staff.objects.filter(is_active=True), required=False)


class SlotSerializer(serializers.Serializer):
    # 顯示層才轉成台北時間（ISO 8601，帶 +08:00）
    start_at = serializers.DateTimeField(default_timezone=SHOP_TZ)
    end_at = serializers.DateTimeField(default_timezone=SHOP_TZ)
    staff_ids = serializers.ListField(child=serializers.IntegerField())
