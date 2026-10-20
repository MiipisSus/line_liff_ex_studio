"""併發建立預約。

這裡必須用 transaction=True：每個執行緒有自己的資料庫連線，
資料要真的 commit 才看得到，也才會真的互相搶鎖。
"""

import threading

import pytest
from django.db import connection

from booking.models import Booking
from booking.services import SlotUnavailable, create_booking
from liff.models import Customer

from .fixtures import NOW, local

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("shop")]


def run_concurrently(n, target):
    """n 個執行緒同時起跑，回傳每個執行緒的結果（Booking 或例外）。"""
    barrier = threading.Barrier(n)
    results = [None] * n

    def worker(i):
        try:
            barrier.wait()
            results[i] = target(i)
        except Exception as exc:  # noqa: BLE001 — 收集起來給測試檢查
            results[i] = exc
        finally:
            connection.close()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    return results


def make_customers(n):
    return [Customer.objects.create(line_user_id=f"U{i:04d}") for i in range(n)]


def test_same_staff_same_slot_only_one_succeeds(haircut, alice):
    n = 10
    customers = make_customers(n)

    results = run_concurrently(n, lambda i: create_booking(customers[i], haircut, local(10), staff=alice, now=NOW))

    successes = [r for r in results if isinstance(r, Booking)]
    failures = [r for r in results if isinstance(r, SlotUnavailable)]
    assert len(successes) == 1
    assert len(failures) == n - 1
    assert Booking.objects.count() == 1


def test_overlapping_slots_only_one_succeeds(haircut, alice):
    # 10:00–11:00 與 10:30–11:30 重疊，不論誰先拿到鎖，另一個都必須失敗
    customers = make_customers(2)
    starts = [local(10), local(10, 30)]

    results = run_concurrently(2, lambda i: create_booking(customers[i], haircut, starts[i], staff=alice, now=NOW))

    assert sum(isinstance(r, Booking) for r in results) == 1
    assert sum(isinstance(r, SlotUnavailable) for r in results) == 1


def test_any_staff_distributes_without_false_failures(haircut, alice, bob):
    # 12:00 Alice 與 Bob 都有空：3 個請求應該 2 個成功（各分到一人）、1 個失敗
    customers = make_customers(3)

    results = run_concurrently(3, lambda i: create_booking(customers[i], haircut, local(12), now=NOW))

    successes = [r for r in results if isinstance(r, Booking)]
    assert len(successes) == 2
    assert {b.staff_id for b in successes} == {alice.pk, bob.pk}
    assert sum(isinstance(r, SlotUnavailable) for r in results) == 1
