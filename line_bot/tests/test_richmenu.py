import json

import pytest
import responses
from django.core.management import CommandError, call_command

from line_bot import client


@pytest.fixture(autouse=True)
def line_settings(settings):
    settings.LINE_MESSAGING_CHANNEL_ACCESS_TOKEN = "test-token"
    settings.LIFF_ID = "1234567890-abcdefgh"


@responses.activate
def test_setup_richmenu_creates_uploads_sets_default_and_removes_old():
    responses.add(responses.GET, f"{client.API_BASE}/richmenu/list", json={"richmenus": [
        {"richMenuId": "old-1", "name": "booking-main"},
        {"richMenuId": "keep", "name": "something-else"},
    ]})
    responses.add(responses.POST, f"{client.API_BASE}/richmenu", json={"richMenuId": "new-1"})
    responses.add(responses.POST, f"{client.DATA_API_BASE}/richmenu/new-1/content", json={})
    responses.add(responses.POST, f"{client.API_BASE}/user/all/richmenu/new-1", json={})
    responses.add(responses.DELETE, f"{client.API_BASE}/richmenu/old-1", json={})

    call_command("setup_richmenu")

    created = json.loads(responses.calls[1].request.body)
    left, right = created["areas"]
    assert left["action"] == {"type": "uri", "label": "我要預約", "uri": "https://liff.line.me/1234567890-abcdefgh"}
    assert right["action"]["data"] == "action=my_bookings"
    assert responses.calls[2].request.headers["Content-Type"] == "image/png"
    # 只刪同名的舊選單，且在新選單設為預設之後才刪
    assert [c.request.method + " " + c.request.url.split("/v2/bot")[1] for c in responses.calls][-1] == "DELETE /richmenu/old-1"


@responses.activate
def test_setup_richmenu_reports_api_error():
    responses.add(responses.GET, f"{client.API_BASE}/richmenu/list", status=401, json={"message": "Authentication failed"})

    with pytest.raises(CommandError, match="401"):
        call_command("setup_richmenu")
