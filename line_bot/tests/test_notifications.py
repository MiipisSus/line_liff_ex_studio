import json

import pytest
import responses

from booking.tests.fixtures import local
from line_bot import client, notifications

PUSH_URL = f"{client.API_BASE}/message/push"


@pytest.fixture
def booking(make_booking, alice):
    return make_booking(alice, local(10), local(11))


@responses.activate
def test_push_payload_and_retry_key(settings, booking):
    settings.LINE_MESSAGING_CHANNEL_ACCESS_TOKEN = "test-token"
    responses.add(responses.POST, PUSH_URL, json={})

    notifications.notify_booking_confirmed(booking)
    notifications.notify_booking_confirmed(booking)

    first, second = responses.calls
    body = json.loads(first.request.body)
    assert body["to"] == "U0001"
    assert body["messages"][0]["contents"]["header"]["contents"][0]["text"] == "預約已確認"
    # 同一筆預約、同一狀態 → 同一個 retry key，LINE 端會去重
    assert first.request.headers["X-Line-Retry-Key"] == second.request.headers["X-Line-Retry-Key"]


@responses.activate
def test_push_failure_is_logged_not_raised(settings, booking, caplog):
    settings.LINE_MESSAGING_CHANNEL_ACCESS_TOKEN = "test-token"
    responses.add(responses.POST, PUSH_URL, status=500)

    notifications.notify_booking_cancelled_by_shop(booking)

    assert "推播失敗" in caplog.text


@responses.activate
def test_skip_when_token_not_configured(settings, booking, caplog):
    settings.LINE_MESSAGING_CHANNEL_ACCESS_TOKEN = ""

    notifications.notify_booking_confirmed(booking)

    assert len(responses.calls) == 0
    assert "略過通知" in caplog.text
