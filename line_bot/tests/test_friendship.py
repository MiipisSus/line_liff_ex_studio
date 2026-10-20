import pytest
import requests
import responses
from django.urls import reverse

from booking.tests.fixtures import NOW, local
from line_bot.client import API_BASE
from line_bot.services import refresh_friendship
from liff.services import SESSION_CUSTOMER_KEY

from .test_webhook import event, post_events

PROFILE_URL = f"{API_BASE}/profile/U0001"


@pytest.fixture(autouse=True)
def line_settings(settings, monkeypatch):
    settings.LINE_MESSAGING_CHANNEL_SECRET = "test-secret"
    settings.LINE_MESSAGING_CHANNEL_ACCESS_TOKEN = "test-token"
    settings.LINE_OA_BASIC_ID = "@shop123"
    monkeypatch.setattr("django.utils.timezone.now", lambda: NOW)


# --- refresh_friendship ------------------------------------------------------


@responses.activate
def test_friend(customer):
    responses.add(responses.GET, PROFILE_URL, json={"userId": "U0001", "displayName": "小明"})

    assert refresh_friendship(customer) is True
    customer.refresh_from_db()
    assert customer.is_line_friend is True


@responses.activate
def test_not_friend_or_blocked_returns_404(customer):
    responses.add(responses.GET, PROFILE_URL, status=404, json={"message": "Not found"})

    assert refresh_friendship(customer) is False
    customer.refresh_from_db()
    assert customer.is_line_friend is False


@responses.activate
@pytest.mark.parametrize("failure", [
    {"status": 500, "json": {}},
    {"body": requests.ConnectionError()},
])
def test_unknown_on_api_error_keeps_previous_value(customer, failure):
    customer.is_line_friend = True
    customer.save()
    responses.add(responses.GET, PROFILE_URL, **failure)

    assert refresh_friendship(customer) is None
    customer.refresh_from_db()
    assert customer.is_line_friend is True


@responses.activate
def test_unknown_when_token_not_configured(settings, customer):
    settings.LINE_MESSAGING_CHANNEL_ACCESS_TOKEN = ""

    assert refresh_friendship(customer) is None
    assert len(responses.calls) == 0


# --- webhook follow / unfollow -------------------------------------------------


@responses.activate
def test_follow_and_unfollow_update_status(client, customer):
    responses.add(responses.POST, f"{API_BASE}/message/reply", json={})

    post_events(client, event("unfollow") | {"replyToken": None})
    customer.refresh_from_db()
    assert customer.is_line_friend is False

    post_events(client, event("follow"))
    customer.refresh_from_db()
    assert customer.is_line_friend is True


# --- 預約成功畫面 -------------------------------------------------------------


@pytest.fixture
def logged_in(client, customer):
    session = client.session
    session[SESSION_CUSTOMER_KEY] = customer.pk
    session.save()
    return client


def submit_booking(logged_in, haircut):
    return logged_in.post(reverse("liff-book-create"), {
        "service": haircut.pk, "staff": "", "start_at": local(12).isoformat(),
    })


@responses.activate
@pytest.mark.usefixtures("shop")
def test_success_page_reminds_non_friend(logged_in, haircut):
    responses.add(responses.GET, PROFILE_URL, status=404, json={"message": "Not found"})

    resp = submit_booking(logged_in, haircut)

    html = resp.content.decode()
    assert resp.status_code == 200
    assert "您尚未加入官方帳號好友" in html
    assert 'data-fallback-url="https://line.me/R/ti/p/@shop123"' in html


@responses.activate
@pytest.mark.usefixtures("shop")
def test_success_page_no_reminder_for_friend(logged_in, haircut):
    responses.add(responses.GET, PROFILE_URL, json={"userId": "U0001"})

    html = submit_booking(logged_in, haircut).content.decode()

    assert "預約已送出" in html
    assert "您尚未加入官方帳號好友" not in html


@responses.activate
@pytest.mark.usefixtures("shop")
def test_booking_succeeds_even_if_friendship_check_fails(logged_in, haircut):
    responses.add(responses.GET, PROFILE_URL, body=requests.ConnectionError())

    resp = submit_booking(logged_in, haircut)

    assert resp.status_code == 200
    assert "預約已送出" in resp.content.decode()
    assert "您尚未加入官方帳號好友" not in resp.content.decode()
