from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from line_bot import client, messages

RICH_MENU_NAME = "booking-main"
IMAGE_PATH = Path(__file__).resolve().parents[2] / "assets" / "richmenu.png"
WIDTH, HEIGHT = 2500, 843


def build_rich_menu():
    half = WIDTH // 2
    return {
        "size": {"width": WIDTH, "height": HEIGHT},
        "selected": True,
        "name": RICH_MENU_NAME,
        "chatBarText": "預約選單",
        "areas": [
            {
                "bounds": {"x": 0, "y": 0, "width": half, "height": HEIGHT},
                # 從聊天室開啟 LIFF，liff.sendMessages() 才能使用
                "action": {"type": "uri", "label": "我要預約", "uri": messages.liff_url()},
            },
            {
                "bounds": {"x": half, "y": 0, "width": WIDTH - half, "height": HEIGHT},
                "action": {
                    "type": "postback", "label": "我的預約", "displayText": "我的預約",
                    "data": messages.postback_data("my_bookings"),
                },
            },
        ],
    }


class Command(BaseCommand):
    help = "建立預約用的 Rich Menu、上傳圖片並設為所有使用者的預設選單（會刪除同名的舊選單）"

    def handle(self, *args, **options):
        try:
            old_ids = [
                m["richMenuId"] for m in client.request("GET", "/richmenu/list")["richmenus"]
                if m["name"] == RICH_MENU_NAME
            ]
            rich_menu_id = client.request("POST", "/richmenu", json=build_rich_menu())["richMenuId"]
            client.request(
                "POST", f"/richmenu/{rich_menu_id}/content", base=client.DATA_API_BASE,
                data=IMAGE_PATH.read_bytes(), headers={"Content-Type": "image/png"},
            )
            client.request("POST", f"/user/all/richmenu/{rich_menu_id}")
            for old_id in old_ids:
                client.request("DELETE", f"/richmenu/{old_id}")
        except client.LineMessagingError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(self.style.SUCCESS(
            f"已建立並設為預設：{rich_menu_id}（刪除舊選單 {len(old_ids)} 個）"
        ))
