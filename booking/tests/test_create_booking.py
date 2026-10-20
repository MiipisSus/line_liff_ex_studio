from datetime import timedelta

import pytest
from django.db import IntegrityError, transaction

from booking.models import Booking, Staff
from booking.services import (
    BookingNotAllowed,
    SlotUnavailable,
    create_booking,
    create_booking_for_shop,
)

from .fixtures import NOW, local

pytestmark = pytest.mark.usefixtures("shop")


def book(customer, service, start, staff=None):
    return create_booking(customer, service, start, staff=staff, now=NOW)


# --- 顧客建立預約 -------------------------------------------------------------


def test_creates_pending_booking_with_computed_end_and_price(customer, haircut, alice):
    booking = book(customer, haircut, local(10), staff=alice)

    assert booking.status == Booking.Status.PENDING
    assert booking.staff == alice
    assert booking.end_at == local(11)
    assert booking.price == 500


def test_price_is_a_snapshot(customer, haircut, alice):
    booking = book(customer, haircut, local(10), staff=alice)
    haircut.price = 800
    haircut.save()

    booking.refresh_from_db()
    assert booking.price == 500


def test_any_staff_assigns_first_available_by_sort_order(customer, haircut, alice, bob):
    assert book(customer, haircut, local(12)).staff == alice
    # Alice 12:00 已被佔用 → 分給 Bob
    assert book(customer, haircut, local(12)).staff == bob
    with pytest.raises(SlotUnavailable):
        book(customer, haircut, local(12))


@pytest.mark.parametrize("start, reason", [
    (local(10, 15), "沒對齊格線"),
    (local(12, 30), "跨到 Alice 午休"),
    (local(18), "下班後"),
    (local(9), "上班前"),
])
def test_rejects_time_outside_available_slots(customer, haircut, alice, start, reason):
    with pytest.raises(SlotUnavailable):
        book(customer, haircut, start, staff=alice)
    assert not Booking.objects.exists()


def test_rejects_already_booked_slot(customer, haircut, alice, make_booking):
    make_booking(alice, local(10, 30), local(11, 30))

    with pytest.raises(SlotUnavailable):
        book(customer, haircut, local(10), staff=alice)


def test_rejects_too_soon(customer, haircut, alice):
    with pytest.raises(SlotUnavailable):
        create_booking(customer, haircut, local(11), staff=alice, now=local(10, 30))


def test_rejects_staff_not_assigned_to_service(customer, haircut):
    outsider = Staff.objects.create(name="Dave")

    with pytest.raises(SlotUnavailable):
        book(customer, haircut, local(10), staff=outsider)


def test_no_show_limit_blocks_booking(shop, customer, haircut, alice, make_booking):
    shop.no_show_limit = 2
    shop.save()
    past = local(10) - timedelta(days=30)
    make_booking(alice, past, past + timedelta(hours=1), status=Booking.Status.NO_SHOW)
    book(customer, haircut, local(10), staff=alice)  # 1 次：仍可預約

    make_booking(alice, past + timedelta(days=1), past + timedelta(days=1, hours=1), status=Booking.Status.NO_SHOW)
    with pytest.raises(BookingNotAllowed):
        book(customer, haircut, local(14), staff=alice)


def test_no_show_limit_zero_means_unlimited(shop, customer, haircut, alice, make_booking):
    shop.no_show_limit = 0
    shop.save()
    past = local(10) - timedelta(days=30)
    for i in range(5):
        start = past + timedelta(days=i)
        make_booking(alice, start, start + timedelta(hours=1), status=Booking.Status.NO_SHOW)

    assert book(customer, haircut, local(10), staff=alice)


# --- 店家插單 ----------------------------------------------------------------


def test_shop_can_book_outside_schedule(customer, haircut, alice):
    booking = create_booking_for_shop(customer, haircut, alice, local(20), allow_outside_schedule=True)

    assert booking.end_at == local(21)


def test_shop_booking_outside_schedule_still_cannot_overlap(customer, haircut, alice, make_booking):
    make_booking(alice, local(20), local(21))

    with pytest.raises(SlotUnavailable):
        create_booking_for_shop(customer, haircut, alice, local(20, 30), allow_outside_schedule=True)


def test_shop_booking_requires_capable_staff(customer, haircut):
    outsider = Staff.objects.create(name="Dave")

    with pytest.raises(SlotUnavailable):
        create_booking_for_shop(customer, haircut, outsider, local(10), allow_outside_schedule=True)


def test_shop_booking_within_schedule_is_validated(customer, haircut, alice):
    with pytest.raises(SlotUnavailable):
        create_booking_for_shop(customer, haircut, alice, local(20))


def test_shop_booking_ignores_no_show_limit(shop, customer, haircut, alice, make_booking):
    shop.no_show_limit = 1
    shop.save()
    past = local(10) - timedelta(days=30)
    make_booking(alice, past, past + timedelta(hours=1), status=Booking.Status.NO_SHOW)

    with pytest.raises(BookingNotAllowed):
        book(customer, haircut, local(10), staff=alice)
    assert create_booking_for_shop(customer, haircut, alice, local(20), allow_outside_schedule=True)


# --- 資料庫約束（最後一道防線）-----------------------------------------------


def test_db_rejects_overlapping_active_bookings(haircut, alice, make_booking):
    make_booking(alice, local(10), local(11))

    with pytest.raises(IntegrityError), transaction.atomic():
        make_booking(alice, local(10, 30), local(11, 30), status=Booking.Status.PENDING)


def test_db_allows_adjacent_bookings(haircut, alice, make_booking):
    make_booking(alice, local(10), local(11))
    make_booking(alice, local(11), local(12))


@pytest.mark.parametrize("status", [Booking.Status.CANCELLED, Booking.Status.COMPLETED, Booking.Status.NO_SHOW])
def test_db_allows_overlap_with_inactive_booking(haircut, alice, make_booking, status):
    make_booking(alice, local(10), local(11), status=status)
    make_booking(alice, local(10), local(11))


def test_db_allows_overlap_for_different_staff(haircut, alice, bob, make_booking):
    make_booking(alice, local(12), local(13))
    make_booking(bob, local(12), local(13))
