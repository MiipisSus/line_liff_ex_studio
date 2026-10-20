from django.core.management.base import BaseCommand
from django_q.models import Schedule

# (名稱, 任務, 每幾分鐘執行一次)
SCHEDULES = [
    ("booking: 預約提醒", "booking.tasks.send_due_reminders", 10),
    ("booking: 未確認自動取消", "booking.tasks.cancel_unconfirmed_bookings", 10),
]


class Command(BaseCommand):
    help = "建立或更新 django-q2 排程（可重複執行）"

    def handle(self, *args, **options):
        for name, func, minutes in SCHEDULES:
            _, created = Schedule.objects.update_or_create(
                name=name,
                defaults={
                    "func": func,
                    "schedule_type": Schedule.MINUTES,
                    "minutes": minutes,
                    "repeats": -1,
                },
            )
            self.stdout.write(f"{'建立' if created else '更新'}：{name}（每 {minutes} 分鐘）")
