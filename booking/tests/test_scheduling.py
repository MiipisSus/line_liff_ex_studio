import threading
from datetime import timedelta

import pytest
import responses
from django.core.management import call_command
from django.db import connection
from django_q.conf import Conf
from django_q.models import Schedule

from booking import services, tasks
from booking.models import Booking
from line_bot import client

from .fixtures import DAY, NOW, local

pytestmark = pytest.mark.usefixtures("shop")


@pytest.fixture
def enabled(shop):
    shop.pending_auto_cancel_hours = 3
    shop.reminder_hours_before = 24
    shop.save()
    return shop


@pytest.fixture
def queued(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "line_bot.notifications.enqueue_booking_notification", lambda booking_id, kind: calls.append((kind, booking_id))
    )
    return calls


# --- 顧客最早可預約時間 ------------------------------------------------------


@pytest.mark.parametrize("min_advance, auto_cancel, expected", [
    (60, 3, 240),   # 自動取消 3 小時 + 1 小時確認緩衝
    (60, 0, 60),    # 不自動取消
    (300, 3, 300),  # 店家設定更嚴格
])
def test_customer_min_advance(shop, min_advance, auto_cancel, expected):
    shop.min_advance_minutes = min_advance
    shop.pending_auto_cancel_hours = auto_cancel
    assert services.customer_min_advance_minutes(shop) == expected


def test_customer_cannot_book_inside_auto_cancel_window(enabled, haircut, alice, customer):
    now = local(8)  # 最早 12:00
    starts = [s.start_at for s in services.get_available_slots(haircut, DAY, staff=alice, now=now)]

    assert min(starts) == local(12)
    assert local(11) not in starts
    with pytest.raises(services.SlotUnavailable):
        services.create_booking(customer, haircut, local(11), staff=alice, now=now)


def test_shop_insert_is_confirmed_and_ignores_customer_advance(enabled, haircut, alice, customer, monkeypatch):
    monkeypatch.setattr("django.utils.timezone.now", lambda: local(8))

    inside = services.create_booking_for_shop(customer, haircut, alice, local(10))
    outside = services.create_booking_for_shop(customer, haircut, alice, local(20), allow_outside_schedule=True)

    assert inside.status == outside.status == Booking.Status.CONFIRMED


# --- 確認時的提醒判斷 --------------------------------------------------------


def test_confirm_inside_reminder_window_counts_as_reminder(enabled, make_booking, alice, queued):
    booking = make_booking(alice, local(10), local(11), status=Booking.Status.PENDING)

    confirmed = services.confirm_booking(booking, now=local(10) - timedelta(hours=20))

    assert confirmed.reminded_at is not None
    assert services.send_due_reminders(now=local(10) - timedelta(hours=19)) == 0


def test_confirm_outside_reminder_window(enabled, make_booking, alice, queued):
    booking = make_booking(alice, local(10), local(11), status=Booking.Status.PENDING)

    assert services.confirm_booking(booking, now=local(10) - timedelta(hours=30)).reminded_at is None


# --- 預約提醒 ----------------------------------------------------------------


def test_reminders_only_for_due_confirmed_bookings(enabled, make_booking, alice, bob, queued, django_capture_on_commit_callbacks):
    now = local(10) - timedelta(hours=23)
    due = make_booking(alice, local(10), local(11))
    make_booking(alice, local(12), local(13), status=Booking.Status.PENDING)  # 未確認
    make_booking(bob, local(12), local(13), status=Booking.Status.CANCELLED)
    later = make_booking(alice, local(10) + timedelta(days=2), local(11) + timedelta(days=2))  # 還沒到提醒時間
    past = make_booking(bob, now - timedelta(hours=2), now - timedelta(hours=1))  # 已開始

    with django_capture_on_commit_callbacks(execute=True):
        assert services.send_due_reminders(now=now) == 1

    assert queued == [("reminder", due.pk)]
    due.refresh_from_db()
    later.refresh_from_db()
    past.refresh_from_db()
    assert due.reminded_at == now
    assert later.reminded_at is None and past.reminded_at is None


def test_reminders_are_idempotent(enabled, make_booking, alice, queued, django_capture_on_commit_callbacks):
    make_booking(alice, local(10), local(11))
    now = local(10) - timedelta(hours=23)

    with django_capture_on_commit_callbacks(execute=True):
        services.send_due_reminders(now=now)
        services.send_due_reminders(now=now + timedelta(minutes=10))

    assert len(queued) == 1


def test_reminders_disabled(shop, make_booking, alice, queued):
    make_booking(alice, local(10), local(11))
    assert services.send_due_reminders(now=local(9)) == 0


def test_reminder_not_queued_if_claim_rolls_back(enabled, make_booking, alice, queued, django_capture_on_commit_callbacks):
    from django.db import transaction
    make_booking(alice, local(10), local(11))

    with django_capture_on_commit_callbacks(execute=True):
        with pytest.raises(RuntimeError), transaction.atomic():
            services.send_due_reminders(now=local(9))
            raise RuntimeError

    assert queued == []
    assert Booking.objects.get().reminded_at is None


# --- 未確認自動取消 ----------------------------------------------------------


def test_auto_cancel_pending_inside_window(enabled, make_booking, alice, bob, queued, django_capture_on_commit_callbacks):
    now = local(10) - timedelta(hours=3)
    due = make_booking(alice, local(10), local(11), status=Booking.Status.PENDING)
    started = make_booking(bob, now - timedelta(hours=1), now, status=Booking.Status.PENDING)  # 排程停機時錯過的
    later = make_booking(alice, local(14), local(15), status=Booking.Status.PENDING)
    confirmed = make_booking(bob, local(12), local(13))

    with django_capture_on_commit_callbacks(execute=True):
        assert services.cancel_unconfirmed_bookings(now=now) == 2

    for b in (due, started, later, confirmed):
        b.refresh_from_db()
    assert due.status == started.status == Booking.Status.CANCELLED
    assert due.cancelled_by == Booking.CancelledBy.SYSTEM
    assert later.status == Booking.Status.PENDING
    assert confirmed.status == Booking.Status.CONFIRMED
    assert sorted(queued) == sorted([("auto_cancelled", due.pk), ("auto_cancelled", started.pk)])


def test_auto_cancel_is_idempotent(enabled, make_booking, alice, queued):
    make_booking(alice, local(10), local(11), status=Booking.Status.PENDING)
    now = local(9)

    assert services.cancel_unconfirmed_bookings(now=now) == 1
    assert services.cancel_unconfirmed_bookings(now=now) == 0


def test_auto_cancel_disabled(shop, make_booking, alice, queued):
    make_booking(alice, local(10), local(11), status=Booking.Status.PENDING)
    assert services.cancel_unconfirmed_bookings(now=local(9)) == 0


def test_auto_cancel_frees_slot(enabled, make_booking, alice, haircut, queued):
    make_booking(alice, local(10), local(11), status=Booking.Status.PENDING)
    services.cancel_unconfirmed_bookings(now=local(9))

    starts = [s.start_at for s in services.get_available_slots(haircut, DAY, staff=alice, now=NOW, min_advance_minutes=0)]
    assert local(10) in starts


# --- 同時執行（transaction=True，多條連線）-----------------------------------


@pytest.mark.django_db(transaction=True)
def test_concurrent_reminder_runs_never_double_claim(enabled, customer, haircut, alice, bob, queued):
    slots = [(alice, 10), (alice, 14), (alice, 16), (bob, 12), (bob, 14), (bob, 15)]
    for staff, hour in slots:
        Booking.objects.create(customer=customer, service=haircut, staff=staff, start_at=local(hour),
                               end_at=local(hour + 1), price=500, status=Booking.Status.CONFIRMED)

    barrier = threading.Barrier(4)
    counts = []

    def run():
        try:
            barrier.wait()
            counts.append(services.send_due_reminders(now=local(9)))
        finally:
            connection.close()

    threads = [threading.Thread(target=run) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    # 4 個工作同時跑，每筆預約只會被認領、排入一次
    assert sum(counts) == len(slots)
    assert len(queued) == len(slots)
    assert len({booking_id for _, booking_id in queued}) == len(slots)


# --- 排程進入點 / 整合 -------------------------------------------------------


def test_tasks_delegate_to_services(enabled, make_booking, alice, queued, monkeypatch):
    monkeypatch.setattr("django.utils.timezone.now", lambda: local(9))
    make_booking(alice, local(10), local(11), status=Booking.Status.PENDING)

    assert tasks.cancel_unconfirmed_bookings() == 1
    assert tasks.send_due_reminders() == 0


def test_setup_schedules_is_idempotent(db):
    call_command("setup_schedules")
    call_command("setup_schedules")

    assert set(Schedule.objects.values_list("func", flat=True)) == {
        "booking.tasks.send_due_reminders", "booking.tasks.cancel_unconfirmed_bookings",
    }
    assert Schedule.objects.count() == 2


@responses.activate
def test_end_to_end_confirm_pushes_via_django_q(enabled, make_booking, alice, settings, monkeypatch,
                                                django_capture_on_commit_callbacks):
    """實際走 django-q2（sync 模式）：確認 → commit → 排入任務 → 推播。"""
    settings.LINE_MESSAGING_CHANNEL_ACCESS_TOKEN = "test-token"
    monkeypatch.setattr(Conf, "SYNC", True)
    responses.add(responses.POST, f"{client.API_BASE}/message/push", json={})
    booking = make_booking(alice, local(10), local(11), status=Booking.Status.PENDING)

    with django_capture_on_commit_callbacks(execute=True):
        services.confirm_booking(booking, now=NOW)

    assert len(responses.calls) == 1
