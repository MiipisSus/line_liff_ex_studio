from datetime import time

from django.core.management.base import BaseCommand
from django.db import transaction

from booking.models import Service, Shop, Staff, WorkingHours


class Command(BaseCommand):
    help = "建立示範用的店家設定、人員、班表與服務（已有人員時不重複建立）"

    @transaction.atomic
    def handle(self, *args, **options):
        if Staff.objects.exists():
            self.stdout.write(self.style.WARNING("已有人員資料，略過"))
            return

        shop = Shop.load()
        shop.name = "示範美髮工作室"
        shop.save()

        amy = Staff.objects.create(name="Amy", sort_order=1)
        ken = Staff.objects.create(name="Ken", sort_order=2)
        # Amy：週二～週六 10–13、14–19；Ken：週三～週日 12–20；週一全店公休
        for weekday in range(1, 6):
            WorkingHours.objects.create(staff=amy, weekday=weekday, start_time=time(10), end_time=time(13))
            WorkingHours.objects.create(staff=amy, weekday=weekday, start_time=time(14), end_time=time(19))
        for weekday in range(2, 7):
            WorkingHours.objects.create(staff=ken, weekday=weekday, start_time=time(12), end_time=time(20))

        for name, minutes, price, staff in [
            ("剪髮", 60, 600, [amy, ken]),
            ("染髮", 120, 2200, [amy]),
            ("洗髮造型", 30, 350, [amy, ken]),
        ]:
            Service.objects.create(name=name, duration_minutes=minutes, price=price).staff.set(staff)

        self.stdout.write(self.style.SUCCESS("已建立示範資料"))
