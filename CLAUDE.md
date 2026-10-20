# CLAUDE.md

LINE 預約系統（LIFF / LINE MINI App + Messaging API + Django 後台）。
目標：可上線、可放進接案作品集的完整系統，給台灣的工作室、美甲、美髮、課程等店家使用。

## 專案原則

- **亮點在後端**：時段計算、併發控制、狀態流轉、排程、通知策略。這些地方要寫得嚴謹。
- **前端不自己設計**：使用現成元件庫與版型，目標是乾淨、一致、看起來專業。不要花時間做客製視覺。
- **每個里程碑都要可展示**：做完一階段就部署上線，即使畫面簡陋也沒關係。
- **AI 功能只當配角**：可以有，但系統的價值不能依賴它。
- 依照下方各里程碑的「完成條件」驗收，達成即過關，不要無限打磨。

## 技術棧

| 層 | 選擇 | 狀態 |
|---|---|---|
| 後端 | Django + Django REST Framework | 確定 |
| 資料庫 | PostgreSQL | 確定 |
| 後台 | Django Admin（可考慮 Unfold 主題） | 確定 |
| LIFF 前端 | Django template + HTMX + Bootstrap 5（CDN） | 確定 |
| 排程 / 背景任務 | django-q2（Django ORM broker，用 PostgreSQL） | 確定 |
| 部署 | 既有 VPS、Nginx、Let's Encrypt、Docker Compose | 確定 |
| 本機 HTTPS 通道 | ngrok 或 cloudflared | 確定 |

技術選擇變更時，請同步更新這張表。

## 本機開發

- 依賴：`pip install -r requirements-dev.txt`（正式環境只裝 `requirements.txt`）。
- 資料庫：本機使用 Postgres.app（`localhost:5432`，資料庫 `line_liff`）；`docker-compose.yml` 目前只有 `db` 服務，尚未實測。測試也跑在 PostgreSQL 上，不使用 SQLite。
- 測試：`pytest`（設定在 `pytest.ini`）。外部 HTTP 呼叫（LINE API）一律用 `responses` mock。共用 fixture 在 `booking/tests/fixtures.py`，由根目錄 `conftest.py` 的 `pytest_plugins` 載入，各 app 直接使用即可，不要再個別 import fixture。
- 示範資料：`python manage.py seed_demo`（已有人員時略過）。
- 後台的 `Booking` 目前唯讀；建立預約與狀態轉換必須經過 service layer。
- LIFF 預約流程在 `liff/views.py`（`/liff/book/...`），每一步回傳 `liff/templates/liff/partials/` 的 HTMX partial，畫面替換到 `#app`：
  - 頁面間的參數一律用 `hx-vals` 傳遞，不要手拼 query string（ISO 時間的 `+08:00` 會被當成空白）。
  - `htmx.config.responseHandling` 已設定 4xx 也替換畫面，錯誤訊息 partial 可以搭配正確的狀態碼（400 / 401 / 403 / 409）。
  - 需要登入的 partial view 用 `@customer_required`，會把顧客放進 `request.customer`。
- 用 ngrok / cloudflared 在手機測試時，`.env` 要設 `DJANGO_BEHIND_PROXY=True`，否則 Django 會把 HTTPS 當成 HTTP，CSRF Origin 檢查失敗（403）。
- **改了 `.env` 必須手動重啟 `runserver`**：autoreload 不會重新讀 `.env`。
- 設定一律透過 `config/settings.py` 的 `env()` / `env_bool()` 讀取；必要變數未設定時啟動即報錯。

## LINE 平台設定

- **Provider**：所有 channel 放在同一個 provider 底下，確保 bot 與 LIFF 取得的 `userId` 一致。
- **LINE MINI App channel**：放 LIFF 頁面，地區選台灣。新的 LIFF 一律建在 MINI App channel，不使用 Messaging API channel 掛 LIFF 的舊做法。
  - LIFF scope 必須勾選 `openid`（否則 `getIDToken()` 回傳 `null`）與 `profile`（否則 ID token 沒有 `name`）。
  - `LINE_CHANNEL_ID` 是 **MINI App channel** 的 ID（即 LIFF ID 中 `-` 前面的數字），用於驗證 ID token 的 `aud`；不是 Messaging API channel 的 ID。
  - LIFF scope 另需勾選 `chat_message.write`（預約成功後 `liff.sendMessages()` 用）。
  - LIFF 畫面尺寸需為 `Full`，且要連結官方帳號（add friend option），`liff.requestFriendship()` 才能彈出加好友視窗；否則改開 `LINE_OA_BASIC_ID` 的加好友連結（選填）。
- **Messaging API channel 設定**：
  - `.env` 填 `LINE_MESSAGING_CHANNEL_SECRET`、`LINE_MESSAGING_CHANNEL_ACCESS_TOKEN`（long-lived）。未設定時 webhook 一律回 400、推播略過。
  - Webhook URL 設為 `https://<網域>/line/webhook/`，開啟「Use webhook」。
  - 在 LINE Official Account Manager 關閉「自動回應訊息」與「加入好友的歡迎訊息」，避免和 bot 重複回覆。
  - Rich Menu 用 `python manage.py setup_richmenu` 建立（圖片在 `line_bot/assets/richmenu.png`，會刪除同名舊選單）。API 設定的預設選單優先於 OA Manager 設定的選單。
- **Messaging API channel**：用於 bot、Rich Menu、通知。
- **LIFF SDK**：使用 CDN 的 `https://static.line-scdn.net/liff/edge/2/sdk.js`；若用 npm 安裝，版本必須 >= 2.31.1（2.27.x 已因安全問題棄用）。
- 機密資料（channel secret、access token、LIFF ID）一律放 `.env`，不得 commit。

## 身分驗證（不可妥協）

1. LIFF 前端呼叫 `liff.getIDToken()`，把 ID token 送到後端。
2. 後端呼叫 LINE 的 verify endpoint 驗證 ID token，並檢查 `aud` 等於 channel ID。
3. 以驗證後的 `sub`（LINE userId）建立或找到 `Customer`，再發放本系統的 session 或 token。
4. **絕對不要信任前端直接傳來的 userId 或 profile。**

目前實作（`liff/services.py`）：
- `POST /liff/auth/line/` 收 `{"id_token": ...}`；除了 LINE 驗證外，本地再檢查 `aud`、`iss`、`exp`、`sub`。無效 token 回 401，LINE 連線失敗回 502。
- 使用 Django session（同網域，不用 JWT）：登入時 `cycle_key()` 後把 `customer_id` 存進 session；之後的 view 一律用 `get_current_customer(request)` 取得目前顧客。`Customer` 不是 Django `User`，Django `User` 只給後台人員使用。
- 每次登入以 ID token 的 `name` 更新 `Customer.display_name`。
- LIFF 端所有 POST 都要經過 Django CSRF 檢查：前端帶 `X-CSRFToken`（HTMX 用 `hx-headers` 設定）。不要為了方便加 `csrf_exempt`；若改用 DRF `APIView`，要確認 CSRF 仍會被檢查（DRF 對未使用 `SessionAuthentication` 的 view 不檢查 CSRF）。

Messaging API webhook 必須驗證 `X-Line-Signature`，驗證失敗一律回 400。

## 核心領域模型

**一個部署只服務一間店**（LINE channel 也是一店一組），其他模型不掛 `Shop` FK。

- `Shop`（`booking`）：單例，一律用 `Shop.load()` 取得。存店家可設定的規則：時段間隔、最少提前預約、最多可預約天數、取消期限 N 小時、no-show 上限。時區固定 `Asia/Taipei`，用程式常數 `SHOP_TZ`，不是欄位。
- `Staff`（`booking`）：服務人員或可預約資源
- `Service`（`booking`）：服務項目、時長、價格、可執行的 Staff（M2M）
- `WorkingHours`（`booking`）：**每位人員各自的**每週班表，同一天可有多段；不支援跨午夜
- `ScheduleException`（`booking`）：`closed`（休假；`staff` 為空 = 全店，時間為空 = 整天）或 `open`（特別上班，必須指定人員與時間，加在當天班表上）。同時段 `closed` 優先於 `open`
- `Customer`（`liff`）：`line_user_id`（unique）、顯示名稱、電話
- `Booking`（`booking`）：Customer、Service、Staff、`start_at`、`end_at`、狀態。no-show 以狀態 `no_show` 表示，不另設欄位。佔用時段的狀態定義在 `Booking.BLOCKING_STATUSES`（`pending`、`confirmed`）

`Booking` 的狀態：`pending` → `confirmed` → `completed`；分支狀態有 `cancelled`、`no_show`。
狀態轉換集中在 service layer 或狀態機函式處理，不要在 view 裡直接改 `status`。

## 預約系統的關鍵規則

- **時間**：資料庫一律存 UTC（`USE_TZ = True`），只在顯示層轉成 `Asia/Taipei`。
- **可預約時段**：由 `WorkingHours` ∪ 特別上班、減去休假、再減去既有 `Booking`，依 `Service` 時長動態計算，不預先產生 slot 資料表。實作在 `booking/services.py` 的 `get_available_slots()`：
  - 開始時間對齊「以當地午夜為基準、間隔為 `slot_interval_minutes`」的格線。
  - 顧客可「不指定人員」：回傳任一位可執行人員有空的時段，`Slot.staff_ids` 列出所有有空的人員，建立預約時再分配。
  - API：`GET /api/services/`、`GET /api/availability/?service=&date=[&staff=]`，時間以 `+08:00` 的 ISO 8601 回傳。
- **防止重複預約**：建立預約時必須放在 `transaction.atomic()` 裡，搭配 `select_for_update()` 或資料庫層級約束（例如 PostgreSQL 的 exclusion constraint）。必須有併發測試覆蓋這段邏輯。
- **排程任務**：以「定期掃描資料庫 + 冪等」實作（例如以 `reminded_at` 標記已提醒），不在建立預約時排 ETA 任務；任務邏輯放 `services.py`，django-q2 只做薄包裝。
- **取消規則**：開始前 N 小時內不可取消；no-show 累積達一定次數時限制預約。N 和次數設計成店家可設定。

## 通知策略（成本相關）

- 由使用者主動觸發的互動（查詢、預約確認），一律用 **reply message**，這類訊息不計費。
- 只有必要時才用 **push**：前一天提醒、店家變更或取消預約。
- 不做全體群發功能，或至少把它限制在後台，並加上確認步驟。
- 每一種通知都要在程式碼中標明是 reply 還是 push，方便估算每月訊息量（`line_bot/client.py` 的 `reply_message()` / `push_message()`，呼叫點加註【reply・不計費】/【push・計費】）。
- LIFF 送出預約不會產生 replyToken，無法直接 reply；MINI App 的 service message 只限通過審核的 MINI App。因此：LIFF 從官方帳號聊天室開啟時，預約成功後以 `liff.sendMessages()` 代替顧客送出「我已完成預約 #ID」，webhook 收到後 reply 明細；不是從聊天室開啟就只顯示 LIFF 成功畫面，不改用 push。

| 通知 | 觸發 | 類型 |
|---|---|---|
| 預約明細 | 預約成功後 LIFF `sendMessages()` → webhook | reply |
| 我的預約列表 | Rich Menu postback 或輸入「我的預約」 | reply |
| 取消確認 / 取消結果 | 列表上的「取消預約」按鈕（postback） | reply |
| 歡迎訊息 | 加入好友（follow） | reply |
| 預約已確認 | 店家在後台確認 | push |
| 預約已被店家取消 | 店家在後台取消 | push |

- push 一律在 `transaction.on_commit` 後發送，失敗只記 log，不回滾狀態；`X-Line-Retry-Key` 由「預約 ID + 狀態」產生，重送不會重複推播。完成、未到、顧客自行取消都不推播。
- webhook 只回覆上述關鍵字與 postback，其他文字不回，保留給店家在 OA Manager 手動回覆。
- **好友狀態**：非好友收不到 push。`Customer.is_line_friend`（`None` = 未知）由 webhook follow / unfollow 更新，預約成功時再以 `GET /v2/bot/profile/{userId}`（不計費，404 = 未加好友或已封鎖）確認，見 `line_bot.services.refresh_friendship()`。確認為非好友時，LIFF 成功畫面提醒並提供「加入官方帳號好友」按鈕；查詢失敗時不提醒，也不影響預約。

## 里程碑

1. ✅ **身分打通**：在 LINE 中打開 LIFF，畫面顯示「你好，{名稱}」，後端資料庫建立 Customer。（已透過 ngrok 在手機上實測通過；尚未部署到 VPS）
2. ✅ **時段模型**：後台可以設定營業時間、服務、人員、例外日，API 能回傳某日可預約的時段。
3. ✅ **建立預約**：LIFF 完成選服務、選時段、確認、送出；併發測試通過。（UI 細節優化留到里程碑 6）
   - 前端只送 `service`、`start_at`、（選填）`staff`；`end_at` 與價格由後端決定。
   - 建立時在交易內重新呼叫 `get_available_slots()` 驗證，並加上 exclusion constraint（同一人員、`BLOCKING_STATUSES` 時間不可重疊）。
   - `Booking` 加 `price` 快照，建立時從 `Service` 複製，之後不隨服務設定變動。
   - 店家插單的 service 函式可帶 `allow_outside_schedule`，略過班表檢查，但仍受重疊約束。
   - 實作：`booking/services.py` 的 `create_booking()`（顧客，檢查 no-show 上限）與 `create_booking_for_shop()`（店家，不檢查 no-show）。併發控制 = 依 pk 順序 `select_for_update()` 鎖住候選人員 → 重新計算時段 → 寫入；exclusion constraint `booking_no_overlap_per_staff` 為最後防線。「不指定人員」分給有空人員中排序最前面的一位。店家插單的後台介面留到里程碑 6。
   - 併發測試在 `booking/tests/test_concurrency.py`（`transaction=True` + 多執行緒）。拿掉鎖時「不指定人員」測試會失敗，修改這段邏輯後務必重跑。
4. **Bot 通知**：預約成功時用 reply 通知；店家改變狀態時用 push 通知。Rich Menu 提供「我要預約」和「我的預約」。（已實作並通過測試，待 LINE 平台設定與手機實測）
   - 店家在已有預約的日期新增休假或修改班表時，後台要列出受影響的預約並提示。
   - 實作：狀態機在 `booking/services.py`（`ALLOWED_TRANSITIONS`，轉換時 `select_for_update` 以資料庫狀態為準）；顧客取消檢查取消期限並記錄 `cancelled_by`。webhook 在 `line_bot/`。後台預約列表以 admin action 操作狀態，店家取消有確認頁；班表 / 例外日存檔後以 `find_bookings_outside_schedule()` 提示受影響預約。
5. **排程**：前一天提醒；逾時未確認的預約自動取消。
6. **後台打磨與文件**：店家操作流程順暢，並撰寫交付用的 README（部署方式、環境變數、操作說明）。
   - 完成預約時可填「實收金額」`final_price`（預設等於 `price`），不做金流。
   - 延長 `end_at`（超時）要透過 service 函式，撞到下一筆預約就拒絕。

## 開發慣例

- 商業邏輯放在 `services.py`；view 只負責輸入驗證與回應。
- 時段計算、狀態轉換、防止重複預約都要有測試（pytest + pytest-django）。
- 遷移檔要一起 commit；不要手動改已經套用過的 migration。
- 新增環境變數時，同步更新 `.env.example`。
- 新增套件時，固定版本寫進 `requirements.txt`（執行期）或 `requirements-dev.txt`（測試 / 開發）。

## 部署注意事項

- 正式環境：`DJANGO_DEBUG=False`，`DJANGO_ALLOWED_HOSTS` 填實際網域（不可用 `*`），`DJANGO_SECRET_KEY` 另外產生。
- migration 會建立 PostgreSQL extension `btree_gist`，資料庫使用者需要有建立 extension 的權限（Docker 官方 postgres image 的預設使用者可以）。
- Nginx 必須設定 `proxy_set_header X-Forwarded-Proto $scheme;`，並搭配 `DJANGO_BEHIND_PROXY=True`；未經代理直接對外時不可開啟此設定（header 可被偽造）。
- 回答與註解使用繁體中文；程式識別字使用英文。