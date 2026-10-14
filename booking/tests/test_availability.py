from datetime import time, timedelta, timezone as dt_timezone

import pytest

from booking.models import Booking, ScheduleException, Service, Staff, WorkingHours
from booking.services import get_available_slots

from .conftest import DAY, NOW, local, hhmm

ALICE_ALL = ["10:00", "10:30", "11:00", "11:30", "12:00",
             "14:00", "14:30", "15:00", "15:30", "16:00", "16:30", "17:00"]

pytestmark = pytest.mark.usefixtures("shop")


def slots(service, day=DAY, staff=None, now=NOW):
    return get_available_slots(service, day, staff=staff, now=now)


# --- 班表 --------------------------------------------------------------------


def test_single_staff_split_shift(haircut, alice):
    # 60 分鐘服務，13:00 前最後一個是 12:00；14:00 開始到 17:00
    assert hhmm(slots(haircut, staff=alice)) == ALICE_ALL


def test_any_staff_unions_slots_and_lists_available_staff(haircut, alice, bob):
    result = {hhmm([s])[0]: s.staff_ids for s in slots(haircut)}

    assert list(result) == ["10:00", "10:30", "11:00", "11:30", "12:00", "12:30", "13:00",
                            "13:30", "14:00", "14:30", "15:00", "15:30", "16:00", "16:30", "17:00"]
    assert result["10:00"] == [alice.pk]
    assert result["12:00"] == [alice.pk, bob.pk]
    assert result["12:30"] == [bob.pk]  # Alice 12:30–13:30 會跨到午休


def test_adjacent_working_hours_are_merged(shop, haircut):
    carol = Staff.objects.create(name="Carol")
    WorkingHours.objects.create(staff=carol, weekday=1, start_time=time(10), end_time=time(12))
    WorkingHours.objects.create(staff=carol, weekday=1, start_time=time(12), end_time=time(13))
    haircut.staff.add(carol)

    assert hhmm(slots(haircut, staff=carol)) == ["10:00", "10:30", "11:00", "11:30", "12:00"]


def test_no_working_hours_on_that_weekday(haircut, alice):
    wednesday = DAY + timedelta(days=1)
    assert slots(haircut, day=wednesday, staff=alice) == []


def test_slot_interval_follows_shop_setting(shop, haircut, alice):
    shop.slot_interval_minutes = 15
    shop.save()

    assert hhmm(slots(haircut, staff=alice))[:9] == [
        "10:00", "10:15", "10:30", "10:45", "11:00", "11:15", "11:30", "11:45", "12:00"
    ]


def test_slots_are_stored_as_aware_utc_equivalent(haircut, alice):
    first = slots(haircut, staff=alice)[0]
    # 台北 10:00 = UTC 02:00
    assert first.start_at.astimezone(dt_timezone.utc).hour == 2
    assert first.end_at - first.start_at == timedelta(minutes=60)


# --- 例外日 ------------------------------------------------------------------


def test_staff_full_day_off(haircut, alice, bob):
    ScheduleException.objects.create(staff=alice, date=DAY, kind="closed")

    assert slots(haircut, staff=alice) == []
    assert all(s.staff_ids == [bob.pk] for s in slots(haircut))


def test_shop_wide_closed(haircut, alice, bob):
    ScheduleException.objects.create(staff=None, date=DAY, kind="closed")

    assert slots(haircut) == []


def test_partial_leave(haircut, alice):
    ScheduleException.objects.create(staff=alice, date=DAY, kind="closed", start_time=time(15), end_time=time(16))

    assert hhmm(slots(haircut, staff=alice)) == [
        "10:00", "10:30", "11:00", "11:30", "12:00", "14:00", "16:00", "16:30", "17:00"
    ]


def test_extra_hours_extend_shift(haircut, alice):
    ScheduleException.objects.create(staff=alice, date=DAY, kind="open", start_time=time(18), end_time=time(20))

    assert hhmm(slots(haircut, staff=alice)) == ALICE_ALL + ["17:30", "18:00", "18:30", "19:00"]


def test_special_opening_on_day_off(haircut, alice):
    wednesday = DAY + timedelta(days=1)
    ScheduleException.objects.create(staff=alice, date=wednesday, kind="open", start_time=time(10), end_time=time(12))

    assert hhmm(slots(haircut, day=wednesday, staff=alice)) == ["10:00", "10:30", "11:00"]


def test_closed_overrides_open(haircut, alice):
    ScheduleException.objects.create(staff=alice, date=DAY, kind="open", start_time=time(18), end_time=time(20))
    ScheduleException.objects.create(staff=None, date=DAY, kind="closed")

    assert slots(haircut, staff=alice) == []


def test_exception_on_other_day_is_ignored(haircut, alice):
    ScheduleException.objects.create(staff=alice, date=DAY + timedelta(days=7), kind="closed")

    assert hhmm(slots(haircut, staff=alice)) == ALICE_ALL


# --- 既有預約 ----------------------------------------------------------------


def test_existing_booking_blocks_overlapping_slots(haircut, alice, make_booking):
    make_booking(alice, local(10, 30), local(11, 30))

    assert hhmm(slots(haircut, staff=alice)) == ["11:30", "12:00"] + ALICE_ALL[5:]


def test_unaligned_booking_end_rounds_up_to_grid(haircut, alice, make_booking):
    make_booking(alice, local(10), local(10, 45))

    assert hhmm(slots(haircut, staff=alice))[:3] == ["11:00", "11:30", "12:00"]


@pytest.mark.parametrize("status, blocks", [
    (Booking.Status.PENDING, True),
    (Booking.Status.CONFIRMED, True),
    (Booking.Status.CANCELLED, False),
    (Booking.Status.COMPLETED, False),
    (Booking.Status.NO_SHOW, False),
])
def test_only_active_bookings_block(haircut, alice, make_booking, status, blocks):
    make_booking(alice, local(10), local(11), status=status)

    assert ("10:00" not in hhmm(slots(haircut, staff=alice))) is blocks


def test_booking_of_other_staff_does_not_block(haircut, alice, bob, make_booking):
    make_booking(bob, local(12), local(16))

    assert hhmm(slots(haircut, staff=alice)) == ALICE_ALL
    assert all(s.staff_ids == [alice.pk] for s in slots(haircut))


def test_booking_on_other_day_does_not_block(haircut, alice, make_booking):
    next_week = DAY + timedelta(days=7)
    make_booking(alice, local(10, day=next_week), local(18, day=next_week))

    assert hhmm(slots(haircut, staff=alice)) == ALICE_ALL


# --- 預約時間範圍 ------------------------------------------------------------


def test_min_advance_excludes_near_slots(haircut, alice):
    now = local(10, 20)  # 最早可預約 11:20 → 11:30

    assert hhmm(slots(haircut, staff=alice, now=now))[:2] == ["11:30", "12:00"]


def test_past_day_returns_nothing(haircut, alice):
    assert slots(haircut, day=DAY - timedelta(days=7), staff=alice) == []


def test_max_advance_days_boundary(shop, haircut, alice):
    shop.max_advance_days = 1
    shop.save()

    assert slots(haircut, staff=alice) != []  # 明天：可以
    assert slots(haircut, day=DAY + timedelta(days=7), staff=alice) == []


def test_today_uses_shop_local_date(haircut, alice):
    # UTC 10/12 23:00 已是台北 10/13 07:00 → DAY 是「今天」，仍可預約
    from datetime import datetime
    now = datetime(2026, 10, 12, 23, 0, tzinfo=dt_timezone.utc)

    assert hhmm(slots(haircut, staff=alice, now=now)) == ALICE_ALL


# --- 服務 / 人員條件 ---------------------------------------------------------


def test_staff_not_assigned_to_service(shop, haircut):
    outsider = Staff.objects.create(name="Dave")
    WorkingHours.objects.create(staff=outsider, weekday=1, start_time=time(10), end_time=time(18))

    assert slots(haircut, staff=outsider) == []


def test_inactive_staff_excluded(haircut, alice, bob):
    bob.is_active = False
    bob.save()

    assert all(s.staff_ids == [alice.pk] for s in slots(haircut))


def test_inactive_service_returns_nothing(haircut):
    haircut.is_active = False
    haircut.save()

    assert slots(haircut) == []


def test_long_service_needs_continuous_free_time(shop, alice):
    perm = Service.objects.create(name="燙髮", duration_minutes=180, price=2000)
    perm.staff.add(alice)

    # 上午只有 3 小時（10–13）剛好一個；下午 14–18 有 14:00、14:30、15:00
    assert hhmm(slots(perm, staff=alice)) == ["10:00", "14:00", "14:30", "15:00"]
