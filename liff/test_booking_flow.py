import pytest
from django.test import Client
from django.urls import reverse

from booking.models import Booking
from booking.tests.conftest import (  # noqa: F401 — pytest fixtures
    DAY, NOW, alice, bob, customer, haircut, local, make_booking, shop,
)
from liff.models import Customer
from liff.services import SESSION_CUSTOMER_KEY

pytestmark = pytest.mark.usefixtures("shop")


@pytest.fixture(autouse=True)
def freeze_now(monkeypatch):
    monkeypatch.setattr("django.utils.timezone.now", lambda: NOW)


def login(client, customer):
    session = client.session
    session[SESSION_CUSTOMER_KEY] = customer.pk
    session.save()
    return client


@pytest.fixture
def logged_in(client, customer):
    return login(client, customer)


def booking_data(service, start, staff=None):
    return {"service": service.pk, "staff": staff.pk if staff else "", "start_at": start.isoformat()}


@pytest.mark.parametrize("name", ["liff-book-services", "liff-book-slots", "liff-book-confirm"])
def test_requires_login(client, name):
    resp = client.get(reverse(name))
    assert resp.status_code == 401
    assert "請關閉後重新開啟" in resp.content.decode()


def test_requires_login_for_create(client, haircut):
    assert client.post(reverse("liff-book-create"), booking_data(haircut, local(10))).status_code == 401


def test_services_page(logged_in, haircut):
    html = logged_in.get(reverse("liff-book-services")).content.decode()
    assert "你好，小明" in html
    assert "剪髮" in html and "NT$ 500" in html


def test_staff_page_lists_any_and_active_staff(logged_in, haircut, alice, bob):
    bob.is_active = False
    bob.save()

    html = logged_in.get(reverse("liff-book-staff", args=[haircut.pk])).content.decode()

    assert "不指定" in html and "Alice" in html and "Bob" not in html


def test_slots_page(logged_in, shop, haircut, alice, bob):
    resp = logged_in.get(reverse("liff-book-slots"), {"service": haircut.pk, "staff": alice.pk, "date": DAY.isoformat()})

    assert resp.status_code == 200
    assert len(resp.context["days"]) == shop.max_advance_days + 1
    assert resp.context["selected"] == DAY
    html = resp.content.decode()
    assert "10:00" in html
    # 時段按鈕帶的是含時區的 ISO 時間
    assert "2026-10-13T10:00:00+08:00" in html
    # 只有選取中的日期是 pressed
    assert html.count('aria-pressed="true"') == 1


def test_slots_page_out_of_range_date_falls_back_to_today(logged_in, haircut):
    resp = logged_in.get(reverse("liff-book-slots"), {"service": haircut.pk, "date": "2030-01-01"})
    assert resp.context["selected"] == NOW.date()


def test_slots_page_invalid_query(logged_in):
    assert logged_in.get(reverse("liff-book-slots"), {"service": 999}).status_code == 400


def test_confirm_page(logged_in, haircut):
    resp = logged_in.get(reverse("liff-book-confirm"), booking_data(haircut, local(10)))

    html = resp.content.decode()
    assert resp.status_code == 200
    assert "不指定（由店家安排）" in html and "2026/10/13 10:00" in html


def test_create_booking(logged_in, customer, haircut, alice):
    resp = logged_in.post(reverse("liff-book-create"), booking_data(haircut, local(12)))

    assert resp.status_code == 200
    booking = Booking.objects.get()
    assert booking.customer == customer
    assert booking.staff == alice
    assert "預約已送出" in resp.content.decode()


def test_create_ignores_client_supplied_customer(logged_in, customer, haircut):
    other = Customer.objects.create(line_user_id="Uother")
    data = booking_data(haircut, local(12)) | {"customer": other.pk}

    logged_in.post(reverse("liff-book-create"), data)

    assert Booking.objects.get().customer == customer


def test_create_taken_slot_returns_409(logged_in, haircut, alice, make_booking):
    make_booking(alice, local(10), local(11))

    resp = logged_in.post(reverse("liff-book-create"), booking_data(haircut, local(10), staff=alice))

    assert resp.status_code == 409
    assert "重新選擇時段" in resp.content.decode()
    assert Booking.objects.count() == 1


def test_create_outside_schedule_returns_409(logged_in, haircut, alice):
    resp = logged_in.post(reverse("liff-book-create"), booking_data(haircut, local(20), staff=alice))
    assert resp.status_code == 409


def test_create_blocked_by_no_show_limit(logged_in, shop, haircut, alice, make_booking):
    shop.no_show_limit = 1
    shop.save()
    make_booking(alice, local(10, day=NOW.date()), local(11, day=NOW.date()), status=Booking.Status.NO_SHOW)

    resp = logged_in.post(reverse("liff-book-create"), booking_data(haircut, local(12)))

    assert resp.status_code == 403
    assert "未到次數已達上限" in resp.content.decode()


@pytest.mark.parametrize("data", [
    {},
    {"service": "999", "start_at": "2026-10-13T10:00:00+08:00"},
    {"service": "SERVICE", "start_at": "not-a-date"},
])
def test_create_invalid_input(logged_in, haircut, data):
    data = {k: (haircut.pk if v == "SERVICE" else v) for k, v in data.items()}
    assert logged_in.post(reverse("liff-book-create"), data).status_code == 400


def test_create_requires_csrf(customer, haircut):
    client = login(Client(enforce_csrf_checks=True), customer)
    assert client.post(reverse("liff-book-create"), booking_data(haircut, local(12))).status_code == 403


def test_create_rejects_get(logged_in):
    assert logged_in.get(reverse("liff-book-create")).status_code == 405
