import time

import requests
from django.conf import settings

from .models import Customer

LINE_VERIFY_URL = "https://api.line.me/oauth2/v2.1/verify"
LINE_ISSUER = "https://access.line.me"
SESSION_CUSTOMER_KEY = "customer_id"


class LineAuthError(Exception):
    """ID token 無效（過期、aud 不符、被竄改等），對應 401。"""


class LineAPIError(Exception):
    """無法連線到 LINE 或 LINE 回傳非預期結果，對應 502。"""


def verify_id_token(id_token):
    """呼叫 LINE verify endpoint 驗證 ID token，回傳 payload。

    LINE 會檢查簽章與 client_id，但仍在本地再檢查 aud / iss / exp，
    不依賴單一層防護。
    """
    channel_id = settings.LINE_CHANNEL_ID
    try:
        resp = requests.post(
            LINE_VERIFY_URL,
            data={"id_token": id_token, "client_id": channel_id},
            timeout=5,
        )
    except requests.RequestException as exc:
        raise LineAPIError("無法連線到 LINE verify endpoint") from exc

    # LINE 對無效 token 回 400（含 error / error_description）
    if resp.status_code == 400:
        raise LineAuthError(resp.json().get("error_description", "invalid id token"))
    if resp.status_code != 200:
        raise LineAPIError(f"LINE verify endpoint 回傳 {resp.status_code}")

    payload = resp.json()
    if payload.get("aud") != channel_id:
        raise LineAuthError("aud 與 channel ID 不符")
    if payload.get("iss") != LINE_ISSUER:
        raise LineAuthError("iss 不正確")
    if payload.get("exp", 0) <= time.time():
        raise LineAuthError("ID token 已過期")
    if not payload.get("sub"):
        raise LineAuthError("缺少 sub")
    return payload


def authenticate_line_user(id_token):
    """驗證 ID token，並以 sub 建立或更新 Customer。"""
    payload = verify_id_token(id_token)
    customer, _ = Customer.objects.update_or_create(
        line_user_id=payload["sub"],
        defaults={"display_name": payload.get("name", "")},
    )
    return customer


def login_customer(request, customer):
    # 更換 session key，避免 session fixation
    request.session.cycle_key()
    request.session[SESSION_CUSTOMER_KEY] = customer.pk


def get_current_customer(request):
    customer_id = request.session.get(SESSION_CUSTOMER_KEY)
    if customer_id is None:
        return None
    return Customer.objects.filter(pk=customer_id).first()
