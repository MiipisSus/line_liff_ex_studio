"""TASKS_SYNC（Fly.io，沒有常駐 qcluster）下的推播失敗與重試。"""

from datetime import timedelta

import pytest
import responses
from django.core.management import CommandError, call_command
from django_q.conf import Conf

from booking.models import Booking
from booking.tests.fixtures import local
from line_bot import client, notifications
from line_bot.models import FailedNotification

PUSH_URL = f"{client.API_BASE}/message/push"


@pytest.fixture(autouse=True)
def sync_tasks(settings, monkeypatch):
    settings.LINE_MESSAGING_CHANNEL_ACCESS_TOKEN = "test-token"
    monkeypatch.setattr(Conf, "SYNC", True)


@pytest.fixture
def booking(make_booking, alice):
    return make_booking(alice, local(10), local(11))


@responses.activate
def test_sync_send(booking):
    responses.add(responses.POST, PUSH_URL, json={})

    notifications.enqueue_booking_notification(booking.pk, "confirmed")

    assert len(responses.calls) == 1
    assert not FailedNotification.objects.exists()


@responses.activate
def test_sync_failure_is_recorded_not_raised(booking):
    responses.add(responses.POST, PUSH_URL, status=500)

    notifications.enqueue_booking_notification(booking.pk, "confirmed")  # 不會丟例外到呼叫端

    failed = FailedNotification.objects.get()
    assert (failed.booking_id, failed.kind, failed.attempts) == (booking.pk, "confirmed", 1)
    assert "500" in failed.last_error


@responses.activate
def test_shop_action_does_not_fail_when_push_fails(booking, make_booking, alice, django_capture_on_commit_callbacks):
    from booking import services
    pending = make_booking(alice, local(14), local(15), status=Booking.Status.PENDING)
    responses.add(responses.POST, PUSH_URL, status=500)

    with django_capture_on_commit_callbacks(execute=True):
        services.confirm_booking(pending)

    pending.refresh_from_db()
    assert pending.status == Booking.Status.CONFIRMED
    assert FailedNotification.objects.filter(booking_id=pending.pk).exists()


@responses.activate
def test_retry_sends_and_clears_failure(booking):
    responses.add(responses.POST, PUSH_URL, status=500)
    notifications.enqueue_booking_notification(booking.pk, "confirmed")
    responses.replace(responses.POST, PUSH_URL, json={})

    assert notifications.retry_failed_notifications() == 1
    assert not FailedNotification.objects.exists()
    # 重試沿用相同 retry key，LINE 端會去重
    assert len({c.request.headers["X-Line-Retry-Key"] for c in responses.calls}) == 1


@responses.activate
def test_retry_keeps_failure_if_still_failing(booking):
    responses.add(responses.POST, PUSH_URL, status=500)
    notifications.enqueue_booking_notification(booking.pk, "confirmed")

    assert notifications.retry_failed_notifications() == 0
    assert FailedNotification.objects.get().attempts == 2


@responses.activate
def test_retry_gives_up_after_24_hours(booking):
    responses.add(responses.POST, PUSH_URL, status=500)
    notifications.enqueue_booking_notification(booking.pk, "confirmed")
    # 超過 24 小時：retry key 去重已失效，放棄以免重複推播
    FailedNotification.objects.update(created_at=FailedNotification.objects.get().created_at - timedelta(hours=25))
    responses.replace(responses.POST, PUSH_URL, json={})
    calls_before = len(responses.calls)

    assert notifications.retry_failed_notifications() == 0
    assert not FailedNotification.objects.exists()
    assert len(responses.calls) == calls_before


@responses.activate
def test_retry_drops_stale_notification(booking):
    responses.add(responses.POST, PUSH_URL, status=500)
    notifications.enqueue_booking_notification(booking.pk, "confirmed")
    Booking.objects.filter(pk=booking.pk).update(status=Booking.Status.CANCELLED)

    assert notifications.retry_failed_notifications() == 1  # 略過也算處理完成
    assert not FailedNotification.objects.exists()


@responses.activate
def test_repeated_failure_for_same_notification_is_one_record(booking):
    responses.add(responses.POST, PUSH_URL, status=500)
    notifications.enqueue_booking_notification(booking.pk, "confirmed")
    notifications.enqueue_booking_notification(booking.pk, "confirmed")

    assert FailedNotification.objects.count() == 1


# --- run_scheduled_jobs ------------------------------------------------------


@responses.activate
def test_run_scheduled_jobs_runs_everything(shop, make_booking, alice, bob, monkeypatch, capsys,
                                            django_capture_on_commit_callbacks):
    shop.pending_auto_cancel_hours = 3
    shop.reminder_hours_before = 24
    shop.save()
    monkeypatch.setattr("django.utils.timezone.now", lambda: local(8))
    responses.add(responses.POST, PUSH_URL, json={})
    make_booking(alice, local(10), local(11), status=Booking.Status.PENDING)  # → 自動取消
    make_booking(bob, local(12), local(13))  # → 提醒

    # 實際執行時是 autocommit；測試包在交易裡，需要手動觸發 on_commit
    with django_capture_on_commit_callbacks(execute=True):
        call_command("run_scheduled_jobs")

    out = capsys.readouterr().out
    assert "未確認自動取消：1" in out and "預約提醒：1" in out
    assert len(responses.calls) == 2  # 兩則推播在同步模式下直接送出


def test_run_scheduled_jobs_continues_after_failure(db, monkeypatch, capsys):
    def boom():
        raise RuntimeError("db down")
    monkeypatch.setattr("booking.tasks.cancel_unconfirmed_bookings", boom)
    from booking.management.commands import run_scheduled_jobs
    monkeypatch.setattr(run_scheduled_jobs, "JOBS", [
        ("未確認自動取消", boom),
        ("預約提醒", lambda: 0),
    ])

    with pytest.raises(CommandError, match="未確認自動取消"):
        call_command("run_scheduled_jobs")
    assert "預約提醒：0" in capsys.readouterr().out
