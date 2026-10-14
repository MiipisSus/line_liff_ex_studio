"""時段計算。

可預約時段 = (每週班表 ∪ 特別上班) − 休假（人員 / 全店） − 既有預約，
再依服務時長與店家的時段間隔切出開始時間。不預先產生 slot 資料表。
"""

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from django.db.models import Q
from django.utils import timezone

from .models import SHOP_TZ, Booking, ScheduleException, Service, Shop, Staff, WorkingHours

# --- 區間運算（純函式，區間皆為半開 [start, end)）-----------------------------------


def merge_intervals(intervals):
    """合併重疊或相接的區間，回傳排序後的結果。"""
    merged = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def subtract_intervals(base, removals):
    """從 base 扣除 removals，兩者都不需要事先排序或合併。"""
    result = merge_intervals(base)
    for r_start, r_end in merge_intervals(removals):
        next_result = []
        for start, end in result:
            if r_end <= start or r_start >= end:
                next_result.append((start, end))
                continue
            if start < r_start:
                next_result.append((start, r_start))
            if r_end < end:
                next_result.append((r_end, end))
        result = next_result
    return result


# --- 時段計算 ---------------------------------------------------------------------


@dataclass
class Slot:
    start_at: datetime  # aware datetime
    end_at: datetime
    staff_ids: list[int] = field(default_factory=list)


def _local_dt(day, t):
    return datetime.combine(day, t, tzinfo=SHOP_TZ)


def _working_intervals(staff_ids, day):
    """每位人員在 day 當天可上班的區間（尚未扣除預約）。"""
    whole_day = (_local_dt(day, time.min), _local_dt(day + timedelta(days=1), time.min))

    base = {sid: [] for sid in staff_ids}
    for wh in WorkingHours.objects.filter(staff_id__in=staff_ids, weekday=day.weekday()):
        base[wh.staff_id].append((_local_dt(day, wh.start_time), _local_dt(day, wh.end_time)))

    shop_closed = []
    staff_closed = {sid: [] for sid in staff_ids}
    exceptions = ScheduleException.objects.filter(
        Q(staff_id__in=staff_ids) | Q(staff__isnull=True), date=day
    )
    for exc in exceptions:
        interval = (_local_dt(day, exc.start_time), _local_dt(day, exc.end_time)) if exc.start_time else whole_day
        if exc.kind == ScheduleException.Kind.OPEN:
            base[exc.staff_id].append(interval)
        elif exc.staff_id is None:
            shop_closed.append(interval)
        else:
            staff_closed[exc.staff_id].append(interval)

    # 先把特別上班併進班表，再扣休假 → closed 優先於 open
    return {sid: subtract_intervals(base[sid], shop_closed + staff_closed[sid]) for sid in staff_ids}


def _booked_intervals(staff_ids, day_start, day_end):
    booked = {sid: [] for sid in staff_ids}
    bookings = Booking.objects.filter(
        staff_id__in=staff_ids,
        status__in=Booking.BLOCKING_STATUSES,
        start_at__lt=day_end,
        end_at__gt=day_start,
    ).values_list("staff_id", "start_at", "end_at")
    for sid, start, end in bookings:
        booked[sid].append((start, end))
    return booked


def get_bookable_staff(service, staff=None):
    qs = service.staff.filter(is_active=True)
    if staff is not None:
        qs = qs.filter(pk=staff.pk)
    return list(qs.values_list("pk", flat=True))


def get_available_slots(service: Service, day: date, staff: Staff | None = None, now: datetime | None = None):
    """回傳 day（店家當地日期）中可預約 service 的時段。

    staff 為 None 代表「不指定人員」：任一位可執行此服務的人員有空就列出，
    Slot.staff_ids 為該時段所有有空的人員，建立預約時再從中分配。
    """
    shop = Shop.load()
    now = now or timezone.now()
    today = now.astimezone(SHOP_TZ).date()

    if not service.is_active or day < today or day > today + timedelta(days=shop.max_advance_days):
        return []

    staff_ids = get_bookable_staff(service, staff)
    if not staff_ids:
        return []

    day_start = _local_dt(day, time.min)
    day_end = _local_dt(day + timedelta(days=1), time.min)
    duration = timedelta(minutes=service.duration_minutes)
    step = timedelta(minutes=shop.slot_interval_minutes)
    earliest = now + timedelta(minutes=shop.min_advance_minutes)

    working = _working_intervals(staff_ids, day)
    booked = _booked_intervals(staff_ids, day_start, day_end)

    slots: dict[datetime, list[int]] = {}
    for sid in staff_ids:
        for free_start, free_end in subtract_intervals(working[sid], booked[sid]):
            # 開始時間對齊以當地午夜為基準的格線，畫面上才會是整齊的 10:00、10:30
            offset = (free_start - day_start) % step
            start = free_start if not offset else free_start + (step - offset)
            while start + duration <= free_end:
                if start >= earliest:
                    slots.setdefault(start, []).append(sid)
                start += step

    return [Slot(start_at=s, end_at=s + duration, staff_ids=slots[s]) for s in sorted(slots)]
