import json
from datetime import timedelta
from functools import wraps

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from booking import services as booking_services
from booking.models import SHOP_TZ, Service, Shop, Weekday
from line_bot import services as line_services

from . import services
from .forms import BookingForm, SlotQueryForm


def index(request):
    return render(request, "liff/index.html", {"liff_id": settings.LIFF_ID})


@require_POST
def line_login(request):
    """以 LIFF ID token 登入。CSRF 由 middleware 檢查（前端帶 X-CSRFToken）。"""
    try:
        id_token = json.loads(request.body).get("id_token")
    except (json.JSONDecodeError, AttributeError):
        id_token = None
    if not isinstance(id_token, str) or not id_token:
        return JsonResponse({"error": "缺少 id_token"}, status=400)

    try:
        customer = services.authenticate_line_user(id_token)
    except services.LineAuthError:
        return JsonResponse({"error": "ID token 驗證失敗"}, status=401)
    except services.LineAPIError:
        return JsonResponse({"error": "暫時無法連線到 LINE，請稍後再試"}, status=502)

    services.login_customer(request, customer)
    return JsonResponse({"customer": {"id": customer.pk, "display_name": customer.display_name}})


# --- 預約流程（HTMX partial）------------------------------------------------------


def _error(request, message, status):
    return render(request, "liff/partials/error.html", {"message": message}, status=status)


def customer_required(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        customer = services.get_current_customer(request)
        if customer is None:
            return _error(request, "登入已過期，請關閉後重新開啟", status=401)
        request.customer = customer
        return view(request, *args, **kwargs)
    return wrapper


@require_GET
@customer_required
def book_services(request):
    services_qs = Service.objects.filter(is_active=True)
    return render(request, "liff/partials/services.html", {
        "customer": request.customer,
        "services": services_qs,
    })


@require_GET
@customer_required
def book_staff(request, service_id):
    service = get_object_or_404(Service, pk=service_id, is_active=True)
    return render(request, "liff/partials/staff.html", {
        "service": service,
        "staff_list": service.staff.filter(is_active=True),
    })


@require_GET
@customer_required
def book_slots(request):
    form = SlotQueryForm(request.GET)
    if not form.is_valid():
        return _error(request, "查詢條件有誤，請重新選擇", status=400)
    service, staff = form.cleaned_data["service"], form.cleaned_data["staff"]

    shop = Shop.load()
    today = timezone.now().astimezone(SHOP_TZ).date()
    days = [today + timedelta(days=i) for i in range(shop.max_advance_days + 1)]
    selected = form.cleaned_data["date"] or today
    if selected not in days:
        selected = today

    return render(request, "liff/partials/slots.html", {
        "service": service,
        "staff": staff,
        "days": [(d, Weekday(d.weekday()).label[-1]) for d in days],
        "selected": selected,
        "slots": booking_services.get_available_slots(service, selected, staff=staff),
    })


@require_GET
@customer_required
def book_confirm(request):
    form = BookingForm(request.GET)
    if not form.is_valid():
        return _error(request, "時段資料有誤，請重新選擇", status=400)
    return render(request, "liff/partials/confirm.html", form.cleaned_data)


@require_POST
@customer_required
def book_create(request):
    form = BookingForm(request.POST)
    if not form.is_valid():
        return _error(request, "時段資料有誤，請重新選擇", status=400)
    data = form.cleaned_data

    try:
        booking = booking_services.create_booking(
            request.customer, data["service"], data["start_at"], staff=data["staff"]
        )
    except booking_services.BookingNotAllowed as exc:
        return _error(request, str(exc), status=403)
    except booking_services.SlotUnavailable as exc:
        return render(request, "liff/partials/slot_taken.html", {
            "message": str(exc), "service": data["service"], "staff": data["staff"],
            "date": data["start_at"].astimezone(SHOP_TZ).date(),
        }, status=409)

    # 非好友收不到確認 / 提醒推播，在成功畫面提醒加好友；無法判斷（None）時不打擾
    is_friend = line_services.refresh_friendship(request.customer)
    return render(request, "liff/partials/success.html", {
        "booking": booking,
        "show_friend_notice": is_friend is False,
        "add_friend_url": f"https://line.me/R/ti/p/{settings.LINE_OA_BASIC_ID}" if settings.LINE_OA_BASIC_ID else "",
    })
