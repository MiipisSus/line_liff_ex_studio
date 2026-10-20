"""預約相關商業邏輯：時段計算、建立預約、狀態轉換。

時段計算：

可預約時段 = (每週班表 ∪ 特別上班) − 休假（人員 / 全店） − 既有預約，
再依服務時長與店家的時段間隔切出開始時間。不預先產生 slot 資料表。
"""

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from line_bot import notifications

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


# --- 建立預約 ---------------------------------------------------------------------


class BookingError(Exception):
    """建立預約失敗的基底類別；message 可直接顯示給使用者。"""


class SlotUnavailable(BookingError):
    """時段不可預約（不在班表內、已被預約、太近或太遠）。對應 409。"""


class BookingNotAllowed(BookingError):
    """顧客目前不能預約（例如 no-show 達上限）。對應 403。"""


def check_customer_can_book(customer, shop):
    if shop.no_show_limit and customer.bookings.filter(status=Booking.Status.NO_SHOW).count() >= shop.no_show_limit:
        raise BookingNotAllowed("未到次數已達上限，暫時無法線上預約，請直接聯繫店家")


def _insert_booking(customer, service, staff_id, start_at):
    try:
        # savepoint：讓 IntegrityError 不會讓外層交易整個失效
        with transaction.atomic():
            return Booking.objects.create(
                customer=customer,
                service=service,
                staff_id=staff_id,
                start_at=start_at,
                end_at=start_at + timedelta(minutes=service.duration_minutes),
                price=service.price,
            )
    except IntegrityError as exc:
        # exclusion constraint 擋下重疊；正常情況下前面的鎖已避免走到這裡
        raise SlotUnavailable("這個時段剛剛被預約了，請選擇其他時段") from exc


def create_booking(customer, service, start_at, staff=None, now=None):
    """顧客線上建立預約（狀態 pending）。會檢查 no-show 上限。"""
    check_customer_can_book(customer, Shop.load())
    return _create_within_schedule(customer, service, start_at, staff, now)


def create_booking_for_shop(customer, service, staff, start_at, allow_outside_schedule=False):
    """店家代客建立預約（插單），不檢查顧客的 no-show 上限。

    allow_outside_schedule=True 時再略過班表與提前 / 最遠預約天數，
    但仍受 exclusion constraint 約束，不能與既有預約重疊。
    """
    if not allow_outside_schedule:
        return _create_within_schedule(customer, service, start_at, staff, now=None)
    if not service.staff.filter(pk=staff.pk).exists():
        raise SlotUnavailable(f"{staff} 不能執行「{service}」")
    with transaction.atomic():
        return _insert_booking(customer, service, staff.pk, start_at)


def _create_within_schedule(customer, service, start_at, staff, now):
    """在可預約時段內建立預約。

    併發控制分兩層：
    1. 鎖住所有候選人員的資料列，同一人員的建立請求依序執行；拿到鎖後才重新計算時段，
       因此一定看得到前一個交易剛建立的預約。「不指定人員」時這也避免兩個請求都分到
       同一人而其中一個失敗（另一位人員明明有空）。
    2. 資料庫的 exclusion constraint 保證同一人員的佔用時間不重疊，即使繞過本函式也一樣。
    """
    with transaction.atomic():
        # 依 pk 排序加鎖，避免不同請求以不同順序加鎖造成死結
        staff_ids = list(
            Staff.objects.select_for_update()
            .filter(pk__in=get_bookable_staff(service, staff))
            .order_by("pk")
            .values_list("pk", flat=True)
        )
        if not staff_ids:
            raise SlotUnavailable("此服務目前沒有可預約的人員")

        day = start_at.astimezone(SHOP_TZ).date()
        slot = next(
            (s for s in get_available_slots(service, day, staff=staff, now=now) if s.start_at == start_at),
            None,
        )
        if slot is None:
            raise SlotUnavailable("這個時段已無法預約，請選擇其他時段")

        # 不指定人員時，分給有空的人員中排序最前面的一位
        return _insert_booking(customer, service, slot.staff_ids[0], start_at)


# --- 狀態轉換 ---------------------------------------------------------------------
#
# pending → confirmed → completed
#    │          ├──────→ no_show
#    └──────────┴──────→ cancelled
#
# 所有狀態變更都必須經過這裡，不要在 view / admin 直接改 status。

ALLOWED_TRANSITIONS = {
    Booking.Status.PENDING: {Booking.Status.CONFIRMED, Booking.Status.CANCELLED},
    Booking.Status.CONFIRMED: {Booking.Status.COMPLETED, Booking.Status.NO_SHOW, Booking.Status.CANCELLED},
}


class InvalidTransition(BookingError):
    """目前狀態不允許這個轉換。"""


class CancelNotAllowed(BookingError):
    """顧客不能取消（不是本人、已過取消期限）。"""


def _transition(booking_id, to_status, **extra_fields):
    """鎖住預約後檢查並套用狀態轉換，回傳更新後的 Booking。"""
    with transaction.atomic():
        booking = Booking.objects.select_for_update().select_related("customer", "service", "staff").get(pk=booking_id)
        if to_status not in ALLOWED_TRANSITIONS.get(booking.status, set()):
            raise InvalidTransition(
                f"預約目前為「{booking.get_status_display()}」，無法變更為「{Booking.Status(to_status).label}」"
            )
        booking.status = to_status
        for name, value in extra_fields.items():
            setattr(booking, name, value)
        booking.save(update_fields=["status", "updated_at", *extra_fields])
        return booking


def confirm_booking(booking):
    """店家確認預約，push 通知顧客。"""
    booking = _transition(booking.pk, Booking.Status.CONFIRMED)
    _notify_on_commit(notifications.notify_booking_confirmed, booking)
    return booking


def cancel_booking_by_shop(booking):
    """店家取消預約，push 通知顧客。"""
    booking = _transition(booking.pk, Booking.Status.CANCELLED, cancelled_by=Booking.CancelledBy.SHOP)
    _notify_on_commit(notifications.notify_booking_cancelled_by_shop, booking)
    return booking


def complete_booking(booking):
    # 不通知：顧客當下就在店裡，不值得花一則 push
    return _transition(booking.pk, Booking.Status.COMPLETED)


def mark_no_show(booking):
    # 不通知，避免與顧客起爭執；累積次數由 check_customer_can_book 使用
    return _transition(booking.pk, Booking.Status.NO_SHOW)


def ensure_customer_can_cancel(booking, now=None):
    """檢查狀態與取消期限；不能取消時丟出 BookingError（訊息可直接給顧客看）。"""
    now = now or timezone.now()
    if Booking.Status.CANCELLED not in ALLOWED_TRANSITIONS.get(booking.status, set()):
        raise CancelNotAllowed(f"這筆預約目前為「{booking.get_status_display()}」，無法取消")
    hours = Shop.load().cancel_deadline_hours
    if booking.start_at - now < timedelta(hours=hours):
        raise CancelNotAllowed(f"開始前 {hours} 小時內無法線上取消，請直接聯繫店家")


def cancel_booking_by_customer(booking_id, customer, now=None):
    """顧客取消自己的預約。開始前 cancel_deadline_hours 小時內不可取消。

    不在這裡發通知：顧客是從聊天室按鈕觸發，由 webhook 以 reply 回覆結果。
    """
    with transaction.atomic():
        booking = Booking.objects.select_for_update().filter(pk=booking_id, customer=customer).first()
        if booking is None:
            raise CancelNotAllowed("找不到這筆預約")
        ensure_customer_can_cancel(booking, now)
        return _transition(booking.pk, Booking.Status.CANCELLED, cancelled_by=Booking.CancelledBy.CUSTOMER)


def _notify_on_commit(func, booking):
    # 交易 commit 後才發送：避免狀態回滾了通知卻已送出；發送失敗也不影響狀態
    transaction.on_commit(lambda: func(booking))


def get_upcoming_bookings(customer, now=None, limit=10):
    now = now or timezone.now()
    return list(
        customer.bookings.filter(status__in=Booking.BLOCKING_STATUSES, start_at__gte=now)
        .select_related("service", "staff")
        .order_by("start_at")[:limit]
    )


# --- 班表異動檢查 -----------------------------------------------------------------


def find_bookings_outside_schedule(staff_ids=None, date_from=None, date_to=None):
    """找出佔用中、但已不在人員可上班時間內的預約（例如新增休假、修改班表之後）。

    預設檢查從今天起到最遠可預約日。回傳 Booking 清單。
    """
    shop = Shop.load()
    today = timezone.now().astimezone(SHOP_TZ).date()
    date_from = date_from or today
    date_to = date_to or today + timedelta(days=shop.max_advance_days)

    qs = Booking.objects.filter(
        status__in=Booking.BLOCKING_STATUSES,
        start_at__gte=_local_dt(date_from, time.min),
        start_at__lt=_local_dt(date_to + timedelta(days=1), time.min),
    ).select_related("customer", "service", "staff").order_by("start_at")
    if staff_ids is not None:
        qs = qs.filter(staff_id__in=staff_ids)

    bookings = list(qs)
    involved_staff = list({b.staff_id for b in bookings})
    affected = []
    working_by_day = {}
    for booking in bookings:
        day = booking.start_at.astimezone(SHOP_TZ).date()
        if day not in working_by_day:
            working_by_day[day] = _working_intervals(involved_staff, day)
        intervals = working_by_day[day][booking.staff_id]
        if not any(start <= booking.start_at and booking.end_at <= end for start, end in intervals):
            affected.append(booking)
    return affected
