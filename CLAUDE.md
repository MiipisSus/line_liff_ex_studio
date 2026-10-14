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
- 測試：`pytest`（設定在 `pytest.ini`）。外部 HTTP 呼叫（LINE API）一律用 `responses` mock。
- 用 ngrok / cloudflared 在手機測試時，`.env` 要設 `DJANGO_BEHIND_PROXY=True`，否則 Django 會把 HTTPS 當成 HTTP，CSRF Origin 檢查失敗（403）。
- **改了 `.env` 必須手動重啟 `runserver`**：autoreload 不會重新讀 `.env`。
- 設定一律透過 `config/settings.py` 的 `env()` / `env_bool()` 讀取；必要變數未設定時啟動即報錯。

## LINE 平台設定

- **Provider**：所有 channel 放在同一個 provider 底下，確保 bot 與 LIFF 取得的 `userId` 一致。
- **LINE MINI App channel**：放 LIFF 頁面，地區選台灣。新的 LIFF 一律建在 MINI App channel，不使用 Messaging API channel 掛 LIFF 的舊做法。
  - LIFF scope 必須勾選 `openid`（否則 `getIDToken()` 回傳 `null`）與 `profile`（否則 ID token 沒有 `name`）。
  - `LINE_CHANNEL_ID` 是 **MINI App channel** 的 ID（即 LIFF ID 中 `-` 前面的數字），用於驗證 ID token 的 `aud`；不是 Messaging API channel 的 ID。
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

## 核心領域模型（草案）

- `Shop`：店家、營業時區（固定 `Asia/Taipei`）
- `Staff`：服務人員或可預約資源
- `Service`：服務項目、時長、價格、可執行的 Staff
- `BusinessHours`：每週營業時段
- `ScheduleException`：公休、臨時休、特別營業
- `Customer`：`line_user_id`（unique）、顯示名稱、電話
- `Booking`：Customer、Service、Staff、`start_at`、`end_at`、狀態、`no_show` 標記

`Booking` 的狀態：`pending` → `confirmed` → `completed`；分支狀態有 `cancelled`、`no_show`。
狀態轉換集中在 service layer 或狀態機函式處理，不要在 view 裡直接改 `status`。

## 預約系統的關鍵規則

- **時間**：資料庫一律存 UTC（`USE_TZ = True`），只在顯示層轉成 `Asia/Taipei`。
- **可預約時段**：由 `BusinessHours` 減去 `ScheduleException`、再減去既有 `Booking`，依 `Service` 時長動態計算，不預先產生 slot 資料表。
- **防止重複預約**：建立預約時必須放在 `transaction.atomic()` 裡，搭配 `select_for_update()` 或資料庫層級約束（例如 PostgreSQL 的 exclusion constraint）。必須有併發測試覆蓋這段邏輯。
- **排程任務**：以「定期掃描資料庫 + 冪等」實作（例如以 `reminded_at` 標記已提醒），不在建立預約時排 ETA 任務；任務邏輯放 `services.py`，django-q2 只做薄包裝。
- **取消規則**：開始前 N 小時內不可取消；no-show 累積達一定次數時限制預約。N 和次數設計成店家可設定。

## 通知策略（成本相關）

- 由使用者主動觸發的互動（查詢、預約確認），一律用 **reply message**，這類訊息不計費。
- 只有必要時才用 **push**：前一天提醒、店家變更或取消預約。
- 不做全體群發功能，或至少把它限制在後台，並加上確認步驟。
- 每一種通知都要在程式碼中標明是 reply 還是 push，方便估算每月訊息量。

## 里程碑

1. ✅ **身分打通**：在 LINE 中打開 LIFF，畫面顯示「你好，{名稱}」，後端資料庫建立 Customer。（已透過 ngrok 在手機上實測通過；尚未部署到 VPS）
2. **時段模型**：後台可以設定營業時間、服務、人員、例外日，API 能回傳某日可預約的時段。
3. **建立預約**：LIFF 完成選服務、選時段、確認、送出；併發測試通過。
4. **Bot 通知**：預約成功時用 reply 通知；店家改變狀態時用 push 通知。Rich Menu 提供「我要預約」和「我的預約」。
5. **排程**：前一天提醒；逾時未確認的預約自動取消。
6. **後台打磨與文件**：店家操作流程順暢，並撰寫交付用的 README（部署方式、環境變數、操作說明）。

## 開發慣例

- 商業邏輯放在 `services.py`；view 只負責輸入驗證與回應。
- 時段計算、狀態轉換、防止重複預約都要有測試（pytest + pytest-django）。
- 遷移檔要一起 commit；不要手動改已經套用過的 migration。
- 新增環境變數時，同步更新 `.env.example`。
- 新增套件時，固定版本寫進 `requirements.txt`（執行期）或 `requirements-dev.txt`（測試 / 開發）。

## 部署注意事項

- 正式環境：`DJANGO_DEBUG=False`，`DJANGO_ALLOWED_HOSTS` 填實際網域（不可用 `*`），`DJANGO_SECRET_KEY` 另外產生。
- Nginx 必須設定 `proxy_set_header X-Forwarded-Proto $scheme;`，並搭配 `DJANGO_BEHIND_PROXY=True`；未經代理直接對外時不可開啟此設定（header 可被偽造）。
- 回答與註解使用繁體中文；程式識別字使用英文。