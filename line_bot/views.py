import json
import logging

from django.http import HttpResponse, HttpResponseBadRequest
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from . import services

logger = logging.getLogger(__name__)


@csrf_exempt  # 由 LINE 平台呼叫，改以 X-Line-Signature 驗證來源
@require_POST
def webhook(request):
    if not services.verify_signature(request.body, request.headers.get("X-Line-Signature", "")):
        logger.warning("webhook 簽章驗證失敗")
        return HttpResponseBadRequest("invalid signature")
    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return HttpResponseBadRequest("invalid json")

    services.handle_events(payload)
    return HttpResponse("OK")
