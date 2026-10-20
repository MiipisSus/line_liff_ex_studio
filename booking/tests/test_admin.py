from datetime import time

import pytest
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.contrib.messages import get_messages
from django.urls import reverse

from booking.models import Booking, ScheduleException, Shop

from .fixtures import DAY, NOW, local


@pytest.mark.parametrize("model", ["shop", "staff", "service", "scheduleexception", "booking"])
def test_admin_changelist_loads(admin_client, model):
    assert admin_client.get(reverse(f"admin:booking_{model}_changelist")).status_code == 200


@pytest.mark.parametrize("model", ["shop", "staff", "service", "scheduleexception"])
def test_admin_add_page_loads(admin_client, model):
    assert admin_client.get(reverse(f"admin:booking_{model}_add")).status_code == 200


def test_shop_is_singleton(admin_client):
    Shop.load()
    assert admin_client.get(reverse("admin:booking_shop_add")).status_code == 403


def test_booking_admin_is_read_only(admin_client):
    assert admin_client.get(reverse("admin:booking_booking_add")).status_code == 403


# --- 預約狀態動作 ------------------------------------------------------------

CHANGELIST = reverse("admin:booking_booking_changelist")


@pytest.fixture
def no_push(monkeypatch):
    monkeypatch.setattr("line_bot.notifications.enqueue_booking_notification", lambda booking_id, kind: None)


def run_action(admin_client, action, bookings, **extra):
    data = {"action": action, ACTION_CHECKBOX_NAME: [b.pk for b in bookings], **extra}
    return admin_client.post(CHANGELIST, data, follow=True)


def texts(resp):
    return [str(m) for m in get_messages(resp.wsgi_request)]


@pytest.mark.usefixtures("shop", "no_push")
def test_confirm_action(admin_client, make_booking, alice):
    pending = make_booking(alice, local(10), local(11), status=Booking.Status.PENDING)
    done = make_booking(alice, local(14), local(15), status=Booking.Status.COMPLETED)

    resp = run_action(admin_client, "confirm", [pending, done])

    pending.refresh_from_db()
    assert pending.status == Booking.Status.CONFIRMED
    msgs = texts(resp)
    assert "已確認 1 筆預約" in msgs
    assert any("無法變更為「已確認」" in m for m in msgs)


@pytest.mark.usefixtures("shop", "no_push")
def test_cancel_action_requires_confirmation(admin_client, make_booking, alice):
    booking = make_booking(alice, local(10), local(11))

    resp = run_action(admin_client, "cancel_by_shop", [booking])
    booking.refresh_from_db()
    assert booking.status == Booking.Status.CONFIRMED
    assert "推播通知" in resp.content.decode()

    run_action(admin_client, "cancel_by_shop", [booking], confirm="yes")
    booking.refresh_from_db()
    assert booking.status == Booking.Status.CANCELLED
    assert booking.cancelled_by == Booking.CancelledBy.SHOP


# --- 班表異動提示 ------------------------------------------------------------


@pytest.fixture
def frozen_now(monkeypatch):
    monkeypatch.setattr("django.utils.timezone.now", lambda: NOW)


@pytest.mark.usefixtures("shop", "frozen_now")
def test_adding_leave_warns_affected_bookings(admin_client, make_booking, alice):
    make_booking(alice, local(10), local(11))

    resp = admin_client.post(reverse("admin:booking_scheduleexception_add"), {
        "staff": alice.pk, "date": DAY.isoformat(), "kind": "closed", "start_time": "", "end_time": "", "note": "",
    }, follow=True)

    assert ScheduleException.objects.count() == 1
    assert any("有 1 筆預約已不在人員的上班時間內" in m for m in texts(resp))


@pytest.mark.usefixtures("shop", "frozen_now")
def test_leave_without_affected_bookings_has_no_warning(admin_client, make_booking, alice):
    make_booking(alice, local(14), local(15))

    resp = admin_client.post(reverse("admin:booking_scheduleexception_add"), {
        "staff": alice.pk, "date": DAY.isoformat(), "kind": "closed",
        "start_time": "10:00", "end_time": "12:00", "note": "",
    }, follow=True)

    assert not any("不在人員的上班時間內" in m for m in texts(resp))


@pytest.mark.usefixtures("shop", "frozen_now")
def test_changing_working_hours_warns(admin_client, make_booking, alice):
    make_booking(alice, local(16), local(17))
    hours = list(alice.working_hours.order_by("start_time"))
    # 把下午班從 14–18 改成 14–15
    data = {
        "name": "Alice", "is_active": "on", "sort_order": "1",
        "working_hours-TOTAL_FORMS": "2", "working_hours-INITIAL_FORMS": "2",
        "working_hours-MIN_NUM_FORMS": "0", "working_hours-MAX_NUM_FORMS": "1000",
    }
    for i, (wh, end) in enumerate(zip(hours, [time(13), time(15)])):
        data |= {
            f"working_hours-{i}-id": wh.pk, f"working_hours-{i}-staff": alice.pk,
            f"working_hours-{i}-weekday": wh.weekday,
            f"working_hours-{i}-start_time": wh.start_time.strftime("%H:%M"),
            f"working_hours-{i}-end_time": end.strftime("%H:%M"),
        }

    resp = admin_client.post(reverse("admin:booking_staff_change", args=[alice.pk]), data, follow=True)

    assert any("有 1 筆預約已不在人員的上班時間內" in m for m in texts(resp))
