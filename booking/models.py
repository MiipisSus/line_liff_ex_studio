from zoneinfo import ZoneInfo

from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import DateTimeRangeField, RangeBoundary, RangeOperators
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Func, Q

# 店家營業時區固定為台北；資料庫一律存 UTC，只在計算本地日期 / 顯示時使用
SHOP_TZ = ZoneInfo("Asia/Taipei")


class Shop(models.Model):
    """店家設定（單例）。一個部署只服務一間店，一律透過 Shop.load() 取得。"""

    name = models.CharField("店名", max_length=100, default="我的店")
    slot_interval_minutes = models.PositiveSmallIntegerField(
        "時段間隔（分鐘）", default=30, help_text="可預約的開始時間以此間隔對齊，例如 30 → 10:00、10:30"
    )
    min_advance_minutes = models.PositiveIntegerField(
        "最少提前預約（分鐘）", default=60, help_text="距離現在不到這個時間的時段不開放預約"
    )
    max_advance_days = models.PositiveSmallIntegerField(
        "最多可預約幾天後", default=30, help_text="0 代表只能預約今天"
    )
    cancel_deadline_hours = models.PositiveSmallIntegerField(
        "取消期限（小時）", default=24, help_text="開始前幾小時內不可由顧客取消"
    )
    no_show_limit = models.PositiveSmallIntegerField(
        "no-show 上限", default=3, help_text="累積達此次數後限制預約；0 代表不限制"
    )

    class Meta:
        verbose_name = "店家設定"
        verbose_name_plural = "店家設定"
        constraints = [
            models.CheckConstraint(condition=Q(pk=1), name="shop_singleton"),
            models.CheckConstraint(condition=Q(slot_interval_minutes__gt=0), name="shop_slot_interval_positive"),
        ]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        shop, _ = cls.objects.get_or_create(pk=1)
        return shop


class Staff(models.Model):
    name = models.CharField("名稱", max_length=50)
    is_active = models.BooleanField("啟用", default=True)
    sort_order = models.PositiveSmallIntegerField("排序", default=0)

    class Meta:
        verbose_name = "人員"
        verbose_name_plural = "人員"
        ordering = ["sort_order", "id"]

    def __str__(self):
        return self.name


class Service(models.Model):
    name = models.CharField("名稱", max_length=100)
    duration_minutes = models.PositiveSmallIntegerField("時長（分鐘）")
    price = models.PositiveIntegerField("價格（元）")
    staff = models.ManyToManyField(Staff, verbose_name="可執行人員", related_name="services", blank=True)
    is_active = models.BooleanField("啟用", default=True)
    sort_order = models.PositiveSmallIntegerField("排序", default=0)

    class Meta:
        verbose_name = "服務項目"
        verbose_name_plural = "服務項目"
        ordering = ["sort_order", "id"]
        constraints = [
            models.CheckConstraint(condition=Q(duration_minutes__gt=0), name="service_duration_positive"),
        ]

    def __str__(self):
        return self.name


class Weekday(models.IntegerChoices):
    # 與 Python date.weekday() 一致：0 = 週一
    MONDAY = 0, "週一"
    TUESDAY = 1, "週二"
    WEDNESDAY = 2, "週三"
    THURSDAY = 3, "週四"
    FRIDAY = 4, "週五"
    SATURDAY = 5, "週六"
    SUNDAY = 6, "週日"


class WorkingHours(models.Model):
    """人員的每週固定班表。同一天可有多筆（例如 10:00–13:00、14:00–19:00）。不支援跨午夜。"""

    staff = models.ForeignKey(Staff, verbose_name="人員", on_delete=models.CASCADE, related_name="working_hours")
    weekday = models.PositiveSmallIntegerField("星期", choices=Weekday.choices)
    start_time = models.TimeField("開始")
    end_time = models.TimeField("結束")

    class Meta:
        verbose_name = "每週班表"
        verbose_name_plural = "每週班表"
        ordering = ["staff", "weekday", "start_time"]
        constraints = [
            models.CheckConstraint(condition=Q(start_time__lt=models.F("end_time")), name="working_hours_start_before_end"),
        ]

    def __str__(self):
        return f"{self.staff} {self.get_weekday_display()} {self.start_time:%H:%M}–{self.end_time:%H:%M}"

    def clean(self):
        if self.start_time and self.end_time and self.start_time >= self.end_time:
            raise ValidationError("結束時間必須晚於開始時間")


class ScheduleException(models.Model):
    """例外日。

    - closed：休假。staff 為空代表全店公休；時間為空代表整天。
    - open：特別上班（加班、平常公休日臨時營業），必須指定人員與時間，會加在當天班表上。
    同一時間若同時有 open 與 closed，closed 優先。
    """

    class Kind(models.TextChoices):
        CLOSED = "closed", "休假"
        OPEN = "open", "特別上班"

    staff = models.ForeignKey(
        Staff, verbose_name="人員", on_delete=models.CASCADE, related_name="schedule_exceptions",
        null=True, blank=True, help_text="留空代表全店",
    )
    date = models.DateField("日期")
    kind = models.CharField("類型", max_length=10, choices=Kind.choices, default=Kind.CLOSED)
    start_time = models.TimeField("開始", null=True, blank=True, help_text="休假時留空代表整天")
    end_time = models.TimeField("結束", null=True, blank=True)
    note = models.CharField("備註", max_length=100, blank=True)

    class Meta:
        verbose_name = "例外日"
        verbose_name_plural = "例外日"
        ordering = ["date", "start_time"]
        constraints = [
            # 時間要嘛都填、要嘛都空
            models.CheckConstraint(
                condition=Q(start_time__isnull=True, end_time__isnull=True)
                | Q(start_time__isnull=False, end_time__isnull=False, start_time__lt=models.F("end_time")),
                name="schedule_exception_valid_time_range",
            ),
            # 特別上班必須指定人員與時間
            models.CheckConstraint(
                condition=~Q(kind="open") | Q(staff__isnull=False, start_time__isnull=False),
                name="schedule_exception_open_requires_staff_and_time",
            ),
        ]

    def __str__(self):
        who = self.staff or "全店"
        when = f"{self.start_time:%H:%M}–{self.end_time:%H:%M}" if self.start_time else "整天"
        return f"{self.date} {who} {self.get_kind_display()} {when}"

    def clean(self):
        if (self.start_time is None) != (self.end_time is None):
            raise ValidationError("開始與結束時間需同時填寫或同時留空")
        if self.start_time and self.end_time and self.start_time >= self.end_time:
            raise ValidationError("結束時間必須晚於開始時間")
        if self.kind == self.Kind.OPEN:
            if self.staff_id is None:
                raise ValidationError({"staff": "特別上班必須指定人員"})
            if self.start_time is None:
                raise ValidationError("特別上班必須填寫時間")


class TsTzRange(Func):
    function = "TSTZRANGE"
    output_field = DateTimeRangeField()


class BookingStatus(models.TextChoices):
    PENDING = "pending", "待確認"
    CONFIRMED = "confirmed", "已確認"
    COMPLETED = "completed", "已完成"
    CANCELLED = "cancelled", "已取消"
    NO_SHOW = "no_show", "未到"


# 會佔用時段的狀態（時段計算與 exclusion constraint 共用）
BLOCKING_STATUSES = (BookingStatus.PENDING, BookingStatus.CONFIRMED)


class Booking(models.Model):
    Status = BookingStatus
    BLOCKING_STATUSES = BLOCKING_STATUSES

    customer = models.ForeignKey(
        "liff.Customer", verbose_name="顧客", on_delete=models.PROTECT, related_name="bookings"
    )
    service = models.ForeignKey(Service, verbose_name="服務", on_delete=models.PROTECT, related_name="bookings")
    staff = models.ForeignKey(Staff, verbose_name="人員", on_delete=models.PROTECT, related_name="bookings")
    start_at = models.DateTimeField("開始時間")
    end_at = models.DateTimeField("結束時間")
    # 建立時從 Service 複製的價格快照，之後不隨服務設定變動
    price = models.PositiveIntegerField("價格（元）")
    # 狀態轉換一律透過 service layer，不要直接改這個欄位
    status = models.CharField("狀態", max_length=20, choices=BookingStatus.choices, default=BookingStatus.PENDING)
    created_at = models.DateTimeField("建立時間", auto_now_add=True)
    updated_at = models.DateTimeField("更新時間", auto_now=True)

    class Meta:
        verbose_name = "預約"
        verbose_name_plural = "預約"
        ordering = ["-start_at"]
        indexes = [models.Index(fields=["staff", "start_at"])]
        constraints = [
            models.CheckConstraint(condition=Q(start_at__lt=models.F("end_at")), name="booking_start_before_end"),
            # 最後一道防線：同一人員佔用中的預約時間不可重疊（區間為 [start, end)，相接不算重疊）
            ExclusionConstraint(
                name="booking_no_overlap_per_staff",
                expressions=[
                    (F("staff"), RangeOperators.EQUAL),
                    (TsTzRange("start_at", "end_at", RangeBoundary()), RangeOperators.OVERLAPS),
                ],
                condition=Q(status__in=BLOCKING_STATUSES),
            ),
        ]

    def __str__(self):
        return f"{self.customer} {self.service} {self.start_at.astimezone(SHOP_TZ):%Y-%m-%d %H:%M}"
