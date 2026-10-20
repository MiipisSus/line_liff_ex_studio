import base64
import hashlib
import hmac
import json
from datetime import timedelta

import pytest
import responses
from django.urls import reverse

from booking.models import Booking
from booking.tests.fixtures import NOW, local
from line_bot import client
from liff.models import Customer

SECRET = "test-secret"
WEBHOOK_URL = reverse("line-webhook")
REPLY_URL = f"{client.API_BASE}/message/reply"
USER_ID = "U0001"  # 與 customer fixture 相同

pytestmark = pytest.mark.usefixtures("shop")


@pytest.fixture(autouse=True)
def line_settings(settings, monkeypatch):
    settings.LINE_MESSAGING_CHANNEL_SECRET = SECRET
    settings.LINE_MESSAGING_CHANNEL_ACCESS_TOKEN = "test-token"
    settings.LIFF_ID = "1234567890-abcdefgh"
    monkeypatch.setattr("django.utils.timezone.now", lambda: NOW)


def sign(body: bytes, secret=SECRET):
    return base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()


def post_events(client_, *events, signature=None):
    body = json.dumps({"destination": "Uxxx", "events": list(events)}).encode()
    return client_.post(
        WEBHOOK_URL, body, content_type="application/json",
        HTTP_X_LINE_SIGNATURE=sign(body) if signature is None else signature,
    )


def event(type_, user_id=USER_ID, source_type="user", **extra):
    return {
        "type": type_, "replyToken": "reply-token", "webhookEventId": "evt",
        "source": {"type": source_type, "userId": user_id}, **extra,
    }


def text_event(text, **kw):
    return event("message", message={"type": "text", "id": "1", "text": text}, **kw)


def postback_event(data, **kw):
    return event("postback", postback={"data": data}, **kw)


def mock_reply():
    responses.add(responses.POST, REPLY_URL, json={})


def replied_messages():
    calls = [c for c in responses.calls if c.request.url == REPLY_URL]
    assert len(calls) == 1, f"預期回覆 1 次，實際 {len(calls)} 次"
    body = json.loads(calls[0].request.body)
    assert body["replyToken"] == "reply-token"
    assert calls[0].request.headers["Authorization"] == "Bearer test-token"
    return body["messages"]


def no_reply_sent():
    return not [c for c in responses.calls if c.request.url == REPLY_URL]


# --- 簽章驗證 ----------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize("signature", ["", "invalid", sign(b"other body")])
def test_invalid_signature_returns_400(client, signature):
    assert post_events(client, signature=signature).status_code == 400


@pytest.mark.django_db
def test_unconfigured_secret_rejects_everything(client, settings):
    settings.LINE_MESSAGING_CHANNEL_SECRET = ""
    body = json.dumps({"events": []}).encode()
    resp = client.post(WEBHOOK_URL, body, content_type="application/json", HTTP_X_LINE_SIGNATURE=sign(body, ""))
    assert resp.status_code == 400


@pytest.mark.django_db
def test_verify_request_with_no_events(client):
    # LINE Developers Console 的「Verify」按鈕會送空的 events
    assert post_events(client).status_code == 200


def test_rejects_get(client):
    assert client.get(WEBHOOK_URL).status_code == 405


# --- 我的預約 ----------------------------------------------------------------


@responses.activate
def test_my_bookings_text_lists_upcoming(client, customer, alice, make_booking):
    booking = make_booking(alice, local(10), local(11), status=Booking.Status.PENDING)
    make_booking(alice, local(14), local(15), status=Booking.Status.CANCELLED)
    mock_reply()

    assert post_events(client, text_event("我的預約")).status_code == 200

    [message] = replied_messages()
    assert message["type"] == "flex"
    bubbles = message["contents"]["contents"]
    assert len(bubbles) == 1
    cancel_action = bubbles[0]["footer"]["contents"][0]["action"]
    assert cancel_action["data"] == f"action=cancel_request&booking={booking.pk}"


@responses.activate
def test_my_bookings_postback(client, customer, alice, make_booking):
    make_booking(alice, local(10), local(11))
    mock_reply()

    post_events(client, postback_event("action=my_bookings"))

    assert replied_messages()[0]["type"] == "flex"


@responses.activate
@pytest.mark.django_db
def test_my_bookings_for_unknown_user_shows_booking_link(client):
    mock_reply()

    post_events(client, text_event("我的預約", user_id="Unever_opened_liff"))

    [message] = replied_messages()
    assert message["type"] == "template"
    assert message["template"]["actions"][0]["uri"] == "https://liff.line.me/1234567890-abcdefgh"


# --- LIFF sendMessages 觸發的預約明細 -----------------------------------------


@responses.activate
def test_booking_created_text_replies_detail(client, customer, alice, make_booking):
    booking = make_booking(alice, local(10), local(11), status=Booking.Status.PENDING)
    mock_reply()

    post_events(client, text_event(f"我已完成預約 #{booking.pk}"))

    [message] = replied_messages()
    assert message["type"] == "flex"
    assert message["contents"]["header"]["contents"][0]["text"] == "預約已送出，待店家確認"


@responses.activate
def test_booking_created_text_for_others_booking_is_ignored(client, alice, make_booking):
    booking = make_booking(alice, local(10), local(11))  # 屬於 U0001
    Customer.objects.create(line_user_id="Uattacker")

    post_events(client, text_event(f"我已完成預約 #{booking.pk}", user_id="Uattacker"))

    assert no_reply_sent()


@responses.activate
@pytest.mark.django_db
def test_other_text_is_not_replied(client, customer):
    post_events(client, text_event("請問有停車位嗎？"))
    assert no_reply_sent()


# --- 取消預約 ----------------------------------------------------------------


@responses.activate
def test_cancel_request_asks_for_confirmation(client, customer, alice, make_booking):
    booking = make_booking(alice, local(10), local(11))
    mock_reply()

    post_events(client, postback_event(f"action=cancel_request&booking={booking.pk}"))

    [message] = replied_messages()
    assert message["template"]["type"] == "confirm"
    assert message["template"]["actions"][0]["data"] == f"action=cancel_confirm&booking={booking.pk}"
    booking.refresh_from_db()
    assert booking.status == Booking.Status.CONFIRMED  # 還沒取消


@responses.activate
def test_cancel_request_within_deadline_explains(client, customer, alice, make_booking):
    soon = NOW + timedelta(hours=3)
    booking = make_booking(alice, soon, soon + timedelta(hours=1))
    mock_reply()

    post_events(client, postback_event(f"action=cancel_request&booking={booking.pk}"))

    [message] = replied_messages()
    assert message == {"type": "text", "text": "開始前 24 小時內無法線上取消，請直接聯繫店家"}


@responses.activate
def test_cancel_confirm_cancels(client, customer, alice, make_booking):
    booking = make_booking(alice, local(10), local(11))
    mock_reply()

    post_events(client, postback_event(f"action=cancel_confirm&booking={booking.pk}"))

    booking.refresh_from_db()
    assert booking.status == Booking.Status.CANCELLED
    assert booking.cancelled_by == Booking.CancelledBy.CUSTOMER
    assert replied_messages()[0]["contents"]["header"]["contents"][0]["text"] == "預約已取消"
    # 顧客自己取消：只有 reply，沒有 push
    assert not [c for c in responses.calls if "/message/push" in c.request.url]


@responses.activate
def test_cancel_confirm_twice_is_safe(client, customer, alice, make_booking):
    booking = make_booking(alice, local(10), local(11))
    mock_reply()
    mock_reply()

    post_events(client, postback_event(f"action=cancel_confirm&booking={booking.pk}"))
    post_events(client, postback_event(f"action=cancel_confirm&booking={booking.pk}"))

    second = json.loads(responses.calls[1].request.body)["messages"][0]
    assert second["type"] == "text" and "無法取消" in second["text"]


@responses.activate
def test_cannot_cancel_others_booking(client, alice, make_booking):
    booking = make_booking(alice, local(10), local(11))  # 屬於 U0001
    Customer.objects.create(line_user_id="Uattacker")
    mock_reply()

    post_events(client, postback_event(f"action=cancel_confirm&booking={booking.pk}", user_id="Uattacker"))

    booking.refresh_from_db()
    assert booking.status == Booking.Status.CONFIRMED
    assert replied_messages() == [{"type": "text", "text": "找不到這筆預約"}]


@responses.activate
@pytest.mark.django_db
@pytest.mark.parametrize("data", ["action=cancel_confirm&booking=abc", "action=cancel_confirm", "action=unknown", ""])
def test_malformed_postback_does_not_crash(client, customer, data):
    responses.add(responses.POST, REPLY_URL, json={})
    assert post_events(client, postback_event(data)).status_code == 200


@responses.activate
@pytest.mark.django_db
def test_cancel_keep(client, customer):
    mock_reply()
    post_events(client, postback_event("action=cancel_keep"))
    assert replied_messages() == [{"type": "text", "text": "好的，已為您保留預約"}]


# --- 其他事件 ----------------------------------------------------------------


@responses.activate
@pytest.mark.django_db
def test_follow_replies_welcome(client):
    mock_reply()
    post_events(client, event("follow"))
    assert "我要預約" in replied_messages()[0]["text"]


@responses.activate
@pytest.mark.django_db
def test_group_events_are_ignored(client, customer):
    post_events(client, text_event("我的預約", source_type="group"))
    assert no_reply_sent()


@responses.activate
def test_reply_failure_still_returns_200(client, customer, alice, make_booking):
    make_booking(alice, local(10), local(11))
    responses.add(responses.POST, REPLY_URL, status=400, json={"message": "Invalid reply token"})

    assert post_events(client, text_event("我的預約"), text_event("我的預約")).status_code == 200
