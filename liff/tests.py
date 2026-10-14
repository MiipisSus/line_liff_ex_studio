import time

import pytest
import requests
import responses
from django.test import Client
from django.urls import reverse

from liff import services
from liff.models import Customer

CHANNEL_ID = "1234567890"
LOGIN_URL = reverse("liff-line-login")


@pytest.fixture(autouse=True)
def line_settings(settings):
    settings.LINE_CHANNEL_ID = CHANNEL_ID


def make_payload(**overrides):
    payload = {
        "iss": "https://access.line.me",
        "sub": "U1234567890abcdef",
        "aud": CHANNEL_ID,
        "exp": int(time.time()) + 3600,
        "iat": int(time.time()),
        "name": "小明",
    }
    payload.update(overrides)
    return payload


def mock_verify(status=200, json=None):
    responses.add(responses.POST, services.LINE_VERIFY_URL, status=status, json=json)


def post_login(client, id_token="dummy-token"):
    return client.post(LOGIN_URL, {"id_token": id_token}, content_type="application/json")


# --- service ---------------------------------------------------------------


@pytest.mark.django_db
@responses.activate
def test_authenticate_creates_customer():
    mock_verify(json=make_payload())

    customer = services.authenticate_line_user("dummy-token")

    assert customer.line_user_id == "U1234567890abcdef"
    assert customer.display_name == "小明"
    # 確認有把 client_id 送給 LINE
    assert f"client_id={CHANNEL_ID}" in responses.calls[0].request.body


@pytest.mark.django_db
@responses.activate
def test_same_sub_does_not_duplicate_and_updates_name():
    mock_verify(json=make_payload(name="舊名字"))
    mock_verify(json=make_payload(name="新名字"))

    first = services.authenticate_line_user("t1")
    second = services.authenticate_line_user("t2")

    assert first.pk == second.pk
    assert Customer.objects.count() == 1
    assert Customer.objects.get().display_name == "新名字"


@pytest.mark.django_db
@responses.activate
@pytest.mark.parametrize(
    "overrides",
    [
        {"aud": "9999999999"},
        {"iss": "https://evil.example.com"},
        {"exp": int(time.time()) - 1},
        {"sub": ""},
    ],
    ids=["wrong-aud", "wrong-iss", "expired", "missing-sub"],
)
def test_rejects_invalid_payload(overrides):
    mock_verify(json=make_payload(**overrides))

    with pytest.raises(services.LineAuthError):
        services.authenticate_line_user("dummy-token")
    assert Customer.objects.count() == 0


@pytest.mark.django_db
@responses.activate
def test_rejects_when_line_returns_400():
    mock_verify(status=400, json={"error": "invalid_request", "error_description": "IdToken expired."})

    with pytest.raises(services.LineAuthError):
        services.authenticate_line_user("dummy-token")


@responses.activate
def test_network_error_raises_api_error():
    responses.add(responses.POST, services.LINE_VERIFY_URL, body=requests.ConnectionError())

    with pytest.raises(services.LineAPIError):
        services.verify_id_token("dummy-token")


# --- view ------------------------------------------------------------------


@pytest.mark.django_db
@responses.activate
def test_login_view_sets_session(client):
    mock_verify(json=make_payload())

    resp = post_login(client)

    assert resp.status_code == 200
    customer = Customer.objects.get()
    assert resp.json() == {"customer": {"id": customer.pk, "display_name": "小明"}}
    assert client.session[services.SESSION_CUSTOMER_KEY] == customer.pk


@pytest.mark.django_db
@responses.activate
def test_login_view_ignores_client_supplied_user_id(client):
    """前端多傳 userId 也不能影響身分，只認 ID token 的 sub。"""
    mock_verify(json=make_payload())

    client.post(
        LOGIN_URL,
        {"id_token": "dummy-token", "line_user_id": "Uattacker"},
        content_type="application/json",
    )

    assert list(Customer.objects.values_list("line_user_id", flat=True)) == ["U1234567890abcdef"]


@pytest.mark.django_db
@responses.activate
def test_login_view_invalid_token_returns_401(client):
    mock_verify(status=400, json={"error": "invalid_request", "error_description": "Invalid IdToken."})

    resp = post_login(client)

    assert resp.status_code == 401
    assert services.SESSION_CUSTOMER_KEY not in client.session


@pytest.mark.django_db
@pytest.mark.parametrize("body", ["not json", "[]", "{}", '{"id_token": 123}'])
def test_login_view_bad_input_returns_400(client, body):
    resp = client.post(LOGIN_URL, body, content_type="application/json")
    assert resp.status_code == 400


@pytest.mark.django_db
@responses.activate
def test_login_view_upstream_error_returns_502(client):
    mock_verify(status=500)

    assert post_login(client).status_code == 502


@pytest.mark.django_db
def test_login_view_requires_csrf():
    client = Client(enforce_csrf_checks=True)
    assert post_login(client).status_code == 403


def test_login_view_rejects_get(client):
    assert client.get(LOGIN_URL).status_code == 405
