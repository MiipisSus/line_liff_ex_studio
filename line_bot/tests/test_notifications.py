import json

import pytest
import responses

from booking.models import Booking
from booking.tests.fixtures import local
from line_bot import client, notifications

PUSH_URL = f"{client.API_BASE}/message/push"


@pytest.fixture(autouse=True)
def token(settings):
    settings.LINE_MESSAGING_CHANNEL_ACCESS_TOKEN = "test-token"


@pytest.fixture
def booking(make_booking, alice):
    return make_booking(alice, local(10), local(11))  # confirmed


@responses.activate
def test_push_payload_and_retry_key(booking):
    responses.add(responses.POST, PUSH_URL, json={})

    assert notifications.send_booking_notification(booking.pk, "confirmed") == "sent"
    notifications.send_booking_notification(booking.pk, "confirmed")
    notifications.send_booking_notification(booking.pk, "reminder")

    first, second, reminder = responses.calls
    body = json.loads(first.request.body)
    assert body["to"] == "U0001"
    assert body["messages"][0]["contents"]["header"]["contents"][0]["text"] == "預約已確認"
    # 同一筆預約、同一種通知 → 同一個 retry key（重試時 LINE 端去重）；不同種類則不同
    assert first.request.headers["X-Line-Retry-Key"] == second.request.headers["X-Line-Retry-Key"]
    assert reminder.request.headers["X-Line-Retry-Key"] != first.request.headers["X-Line-Retry-Key"]


@responses.activate
def test_push_failure_raises_for_retry(booking):
    responses.add(responses.POST, PUSH_URL, status=500)

    with pytest.raises(client.LineMessagingError):
        notifications.send_booking_notification(booking.pk, "confirmed")


@responses.activate
@pytest.mark.parametrize("status, kind", [
    (Booking.Status.CANCELLED, "confirmed"),  # 確認後又被取消：不再送「已確認」
    (Booking.Status.CANCELLED, "reminder"),
    (Booking.Status.CONFIRMED, "cancelled_by_shop"),
])
def test_stale_notification_is_skipped(make_booking, alice, status, kind):
    booking = make_booking(alice, local(10), local(11), status=status)

    assert notifications.send_booking_notification(booking.pk, kind) == "skipped: stale"
    assert len(responses.calls) == 0


@responses.activate
def test_skip_known_non_friend(booking, customer):
    customer.is_line_friend = False
    customer.save()

    assert notifications.send_booking_notification(booking.pk, "confirmed") == "skipped: not friend"
    assert len(responses.calls) == 0


@responses.activate
def test_skip_when_token_not_configured(settings, booking, caplog):
    settings.LINE_MESSAGING_CHANNEL_ACCESS_TOKEN = ""

    assert notifications.send_booking_notification(booking.pk, "confirmed") == "skipped: no token"
    assert len(responses.calls) == 0


def test_deleted_booking_is_skipped(db):
    assert notifications.send_booking_notification(999, "confirmed") == "skipped: stale"


def test_unknown_kind_rejected():
    with pytest.raises(ValueError):
        notifications.enqueue_booking_notification(1, "promotion")
