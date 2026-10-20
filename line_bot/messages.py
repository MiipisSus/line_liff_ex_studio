"""LINE 訊息內容（Flex / template / text）的組裝。只負責長相，不發送。"""

from urllib.parse import urlencode

from django.conf import settings

from booking.models import SHOP_TZ, Booking

STATUS_COLORS = {
    Booking.Status.PENDING: "#B7791F",
    Booking.Status.CONFIRMED: "#2F855A",
    Booking.Status.CANCELLED: "#9B2C2C",
}


def liff_url():
    return f"https://liff.line.me/{settings.LIFF_ID}"


def postback_data(action, **params):
    return urlencode({"action": action, **params})


def text(message):
    return {"type": "text", "text": message}


def _row(label, value, color=None):
    value_text = {"type": "text", "text": value, "size": "sm", "flex": 4, "wrap": True}
    if color:
        value_text["color"] = color
    return {
        "type": "box", "layout": "horizontal", "spacing": "md",
        "contents": [{"type": "text", "text": label, "size": "sm", "color": "#888888", "flex": 2}, value_text],
    }


def format_booking_time(booking):
    start = booking.start_at.astimezone(SHOP_TZ)
    end = booking.end_at.astimezone(SHOP_TZ)
    weekday = "一二三四五六日"[start.weekday()]
    return f"{start:%Y/%m/%d}（{weekday}）{start:%H:%M}–{end:%H:%M}"


def booking_bubble(booking, title, show_cancel=False):
    bubble = {
        "type": "bubble",
        "header": {
            "type": "box", "layout": "vertical",
            "contents": [{"type": "text", "text": title, "weight": "bold", "size": "lg"}],
        },
        "body": {
            "type": "box", "layout": "vertical", "spacing": "sm",
            "contents": [
                _row("服務", booking.service.name),
                _row("人員", booking.staff.name),
                _row("時間", format_booking_time(booking)),
                _row("價格", f"NT$ {booking.price}"),
                _row("狀態", booking.get_status_display(), STATUS_COLORS.get(booking.status)),
            ],
        },
    }
    if show_cancel:
        bubble["footer"] = {
            "type": "box", "layout": "vertical",
            "contents": [{
                "type": "button", "style": "secondary", "height": "sm",
                "action": {
                    "type": "postback", "label": "取消預約", "displayText": "我要取消這筆預約",
                    "data": postback_data("cancel_request", booking=booking.pk),
                },
            }],
        }
    return bubble


def booking_detail(booking, title):
    return {"type": "flex", "altText": f"{title}：{format_booking_time(booking)}", "contents": booking_bubble(booking, title)}


def booking_list(bookings):
    if not bookings:
        return {
            "type": "template", "altText": "目前沒有預約",
            "template": {
                "type": "buttons", "text": "目前沒有即將到來的預約",
                "actions": [{"type": "uri", "label": "我要預約", "uri": liff_url()}],
            },
        }
    return {
        "type": "flex", "altText": f"您有 {len(bookings)} 筆預約",
        "contents": {
            "type": "carousel",
            "contents": [booking_bubble(b, "我的預約", show_cancel=True) for b in bookings],
        },
    }


def cancel_confirm(booking):
    return {
        "type": "template", "altText": "確定要取消預約嗎？",
        "template": {
            "type": "confirm",
            "text": f"確定要取消 {format_booking_time(booking)} 的「{booking.service.name}」嗎？",
            "actions": [
                {"type": "postback", "label": "確定取消", "displayText": "確定取消",
                 "data": postback_data("cancel_confirm", booking=booking.pk)},
                {"type": "postback", "label": "保留", "displayText": "保留預約",
                 "data": postback_data("cancel_keep")},
            ],
        },
    }
