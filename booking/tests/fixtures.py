from datetime import date, datetime, time

import pytest

from booking.models import SHOP_TZ, Booking, Service, Shop, Staff, WorkingHours
from liff.models import Customer

# 固定「現在」為 2026-10-12（週一）08:00 台北，目標日為隔天週二
NOW = datetime(2026, 10, 12, 8, 0, tzinfo=SHOP_TZ)
DAY = date(2026, 10, 13)
TUESDAY = 1


def local(hour, minute=0, day=DAY):
    return datetime.combine(day, time(hour, minute), tzinfo=SHOP_TZ)


def hhmm(slots):
    return [s.start_at.astimezone(SHOP_TZ).strftime("%H:%M") for s in slots]


@pytest.fixture
def shop(db):
    shop = Shop.load()
    shop.slot_interval_minutes = 30
    shop.min_advance_minutes = 60
    shop.max_advance_days = 30
    shop.save()
    return shop


@pytest.fixture
def alice(db):
    staff = Staff.objects.create(name="Alice", sort_order=1)
    WorkingHours.objects.create(staff=staff, weekday=TUESDAY, start_time=time(10), end_time=time(13))
    WorkingHours.objects.create(staff=staff, weekday=TUESDAY, start_time=time(14), end_time=time(18))
    return staff


@pytest.fixture
def bob(db):
    staff = Staff.objects.create(name="Bob", sort_order=2)
    WorkingHours.objects.create(staff=staff, weekday=TUESDAY, start_time=time(12), end_time=time(16))
    return staff


@pytest.fixture
def haircut(db, alice, bob):
    service = Service.objects.create(name="剪髮", duration_minutes=60, price=500)
    service.staff.set([alice, bob])
    return service


@pytest.fixture
def customer(db):
    return Customer.objects.create(line_user_id="U0001", display_name="小明")


@pytest.fixture
def make_booking(customer, haircut):
    def _make(staff, start, end, status=Booking.Status.CONFIRMED):
        return Booking.objects.create(
            customer=customer, service=haircut, staff=staff, start_at=start, end_at=end, status=status,
            price=haircut.price,
        )
    return _make
