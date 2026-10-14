import pytest
from django.urls import reverse

from booking.models import Shop


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
