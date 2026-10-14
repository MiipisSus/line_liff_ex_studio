import json

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST

from . import services


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
