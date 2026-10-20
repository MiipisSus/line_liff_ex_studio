import pytest
from django.urls import reverse

from booking.models import Service

from .fixtures import DAY, NOW

AVAILABILITY_URL = reverse("api-availability")


@pytest.fixture(autouse=True)
def freeze_now(monkeypatch):
    monkeypatch.setattr("booking.services.timezone.now", lambda: NOW)


@pytest.mark.usefixtures("shop")
def test_service_list(client, haircut, alice, bob):
    bob.is_active = False
    bob.save()
    Service.objects.create(name="停用的服務", duration_minutes=30, price=100, is_active=False)

    resp = client.get(reverse("api-service-list"))

    assert resp.status_code == 200
    assert resp.json() == [{
        "id": haircut.pk, "name": "剪髮", "duration_minutes": 60, "price": 500,
        "staff": [{"id": alice.pk, "name": "Alice"}],
    }]


@pytest.mark.usefixtures("shop")
def test_availability_returns_local_iso_times(client, haircut, alice, bob):
    resp = client.get(AVAILABILITY_URL, {"service": haircut.pk, "date": DAY.isoformat()})

    assert resp.status_code == 200
    data = resp.json()
    assert data["date"] == "2026-10-13"
    assert data["slots"][0] == {
        "start_at": "2026-10-13T10:00:00+08:00",
        "end_at": "2026-10-13T11:00:00+08:00",
        "staff_ids": [alice.pk],
    }


@pytest.mark.usefixtures("shop")
def test_availability_filters_by_staff(client, haircut, alice, bob):
    resp = client.get(AVAILABILITY_URL, {"service": haircut.pk, "date": DAY.isoformat(), "staff": bob.pk})

    assert resp.status_code == 200
    assert {tuple(s["staff_ids"]) for s in resp.json()["slots"]} == {(bob.pk,)}


@pytest.mark.usefixtures("shop")
@pytest.mark.parametrize("params", [
    {},
    {"date": "2026-10-13"},  # 缺 service
    {"service": "999", "date": "2026-10-13"},  # 不存在
    {"service": "SERVICE", "date": "13/10/2026"},  # 日期格式錯誤
    {"service": "SERVICE"},  # 缺 date
])
def test_availability_validation_errors(client, haircut, params):
    params = {k: (str(haircut.pk) if v == "SERVICE" else v) for k, v in params.items()}

    assert client.get(AVAILABILITY_URL, params).status_code == 400


@pytest.mark.usefixtures("shop")
def test_availability_rejects_inactive_service(client, haircut):
    haircut.is_active = False
    haircut.save()

    resp = client.get(AVAILABILITY_URL, {"service": haircut.pk, "date": DAY.isoformat()})

    assert resp.status_code == 400
