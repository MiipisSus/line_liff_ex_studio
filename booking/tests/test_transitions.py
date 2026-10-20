from datetime import time, timedelta

import pytest
from django.db import transaction

from booking import services
from booking.models import Booking, ScheduleException

from .fixtures import DAY, NOW, local

pytestmark = pytest.mark.usefixtures("shop")


@pytest.fixture
def pushed(monkeypatch):
    """攔截排入的推播任務，記錄 (通知種類, booking id)。"""
    calls = []
    monkeypatch.setattr(
        "line_bot.notifications.enqueue_booking_notification", lambda booking_id, kind: calls.append((kind, booking_id))
    )
    return calls


@pytest.fixture
def pending(make_booking, alice):
    return make_booking(alice, local(10), local(11), status=Booking.Status.PENDING)


# --- 店家操作 ----------------------------------------------------------------


def test_confirm_pushes_after_commit(pending, pushed, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        booking = services.confirm_booking(pending)

    assert booking.status == Booking.Status.CONFIRMED
    assert pushed == [("confirmed", pending.pk)]


def test_no_push_when_transaction_rolls_back(pending, pushed, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        with pytest.raises(RuntimeError), transaction.atomic():
            services.confirm_booking(pending)
            raise RuntimeError("後續步驟失敗")

    pending.refresh_from_db()
    assert pending.status == Booking.Status.PENDING
    assert pushed == []


def test_cancel_by_shop(pending, pushed, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        booking = services.cancel_booking_by_shop(pending)

    assert booking.status == Booking.Status.CANCELLED
    assert booking.cancelled_by == Booking.CancelledBy.SHOP
    assert pushed == [("cancelled_by_shop", pending.pk)]


@pytest.mark.parametrize("func, expected", [
    (services.complete_booking, Booking.Status.COMPLETED),
    (services.mark_no_show, Booking.Status.NO_SHOW),
])
def test_complete_and_no_show_do_not_push(pending, pushed, django_capture_on_commit_callbacks, func, expected):
    services.confirm_booking(pending)
    pushed.clear()

    with django_capture_on_commit_callbacks(execute=True):
        assert func(pending).status == expected
    assert pushed == []


@pytest.mark.parametrize("start_status, func", [
    (Booking.Status.PENDING, services.complete_booking),  # 未確認不能直接完成
    (Booking.Status.PENDING, services.mark_no_show),
    (Booking.Status.CONFIRMED, services.confirm_booking),
    (Booking.Status.CANCELLED, services.confirm_booking),
    (Booking.Status.COMPLETED, services.cancel_booking_by_shop),
    (Booking.Status.NO_SHOW, services.complete_booking),
])
def test_invalid_transitions(make_booking, alice, pushed, start_status, func):
    booking = make_booking(alice, local(10), local(11), status=start_status)

    with pytest.raises(services.InvalidTransition):
        func(booking)
    booking.refresh_from_db()
    assert booking.status == start_status


def test_transition_uses_fresh_status(pending, pushed):
    # 手上的物件是舊的（pending），資料庫已被取消 → 以資料庫為準
    Booking.objects.filter(pk=pending.pk).update(status=Booking.Status.CANCELLED)

    with pytest.raises(services.InvalidTransition):
        services.confirm_booking(pending)


def test_cancelled_booking_frees_slot(pending, pushed, haircut, alice):
    def starts():
        return [s.start_at for s in services.get_available_slots(haircut, DAY, staff=alice, now=NOW)]

    assert local(10) not in starts()
    services.cancel_booking_by_shop(pending)
    assert local(10) in starts()


# --- 顧客取消 ----------------------------------------------------------------


def test_customer_cancel_before_deadline(pending, customer, pushed, django_capture_on_commit_callbacks):
    # 取消期限 24 小時；NOW 距離 10/13 10:00 還有 26 小時
    with django_capture_on_commit_callbacks(execute=True):
        booking = services.cancel_booking_by_customer(pending.pk, customer, now=NOW)

    assert booking.status == Booking.Status.CANCELLED
    assert booking.cancelled_by == Booking.CancelledBy.CUSTOMER
    assert pushed == []  # 由 webhook reply 回覆，不推播


def test_customer_cancel_within_deadline(pending, customer):
    with pytest.raises(services.CancelNotAllowed, match="24 小時內"):
        services.cancel_booking_by_customer(pending.pk, customer, now=local(10) - timedelta(hours=23))


def test_customer_cancel_deadline_follows_shop_setting(shop, pending, customer):
    shop.cancel_deadline_hours = 2
    shop.save()

    booking = services.cancel_booking_by_customer(pending.pk, customer, now=local(10) - timedelta(hours=3))
    assert booking.status == Booking.Status.CANCELLED


def test_customer_cannot_cancel_others_booking(pending):
    from liff.models import Customer
    other = Customer.objects.create(line_user_id="Uother")

    with pytest.raises(services.CancelNotAllowed, match="找不到"):
        services.cancel_booking_by_customer(pending.pk, other, now=NOW)
    pending.refresh_from_db()
    assert pending.status == Booking.Status.PENDING


@pytest.mark.parametrize("status", [Booking.Status.CANCELLED, Booking.Status.COMPLETED, Booking.Status.NO_SHOW])
def test_customer_cannot_cancel_finished_booking(make_booking, alice, customer, status):
    booking = make_booking(alice, local(10), local(11), status=status)

    with pytest.raises(services.CancelNotAllowed, match="無法取消"):
        services.cancel_booking_by_customer(booking.pk, customer, now=NOW)


def test_upcoming_bookings(make_booking, alice, customer):
    future = make_booking(alice, local(10), local(11))
    make_booking(alice, local(12), local(13), status=Booking.Status.CANCELLED)
    make_booking(alice, local(10) - timedelta(days=7), local(11) - timedelta(days=7))  # 過去

    assert services.get_upcoming_bookings(customer, now=NOW) == [future]


# --- 班表異動檢查 ------------------------------------------------------------


@pytest.fixture
def frozen_now(monkeypatch):
    monkeypatch.setattr("django.utils.timezone.now", lambda: NOW)


@pytest.mark.usefixtures("frozen_now")
def test_find_bookings_outside_schedule(make_booking, alice, bob):
    inside = make_booking(alice, local(14), local(15))
    on_leave = make_booking(alice, local(10), local(11))
    make_booking(alice, local(15), local(16), status=Booking.Status.CANCELLED)
    ScheduleException.objects.create(staff=alice, date=DAY, kind="closed", start_time=time(9), end_time=time(12))

    affected = services.find_bookings_outside_schedule()

    assert affected == [on_leave]
    assert inside not in affected


@pytest.mark.usefixtures("frozen_now")
def test_find_bookings_outside_schedule_after_shop_closed(make_booking, alice, bob):
    a = make_booking(alice, local(10), local(11))
    b = make_booking(bob, local(12), local(13))
    ScheduleException.objects.create(staff=None, date=DAY, kind="closed")

    assert services.find_bookings_outside_schedule(date_from=DAY, date_to=DAY) == [a, b]


@pytest.mark.usefixtures("frozen_now")
def test_find_bookings_outside_schedule_shop_inserted(customer, haircut, alice):
    # 店家插單排在班表外的預約，也會被列出（提醒店家這筆不在班表內）
    booking = services.create_booking_for_shop(customer, haircut, alice, local(20), allow_outside_schedule=True)

    assert services.find_bookings_outside_schedule(staff_ids=[alice.pk]) == [booking]
