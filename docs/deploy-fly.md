# 部署到 Fly.io（壓在免費額度內）

架構：

| 元件 | 設定 | 費用重點 |
|---|---|---|
| web | 1 台 `shared-cpu-1x` / 256MB，`auto_stop_machines = "suspend"`、`min_machines_running = 0` | 沒流量時停機不計 CPU / 記憶體 |
| 排程 | scheduled machine 每小時喚醒一次跑 `run_scheduled_jobs`，跑完就關 | 每次只跑幾秒 |
| 資料庫 | 外部免費 PostgreSQL（建議 Neon） | **不要開 Fly Managed Postgres** |
| IP | 只用共享 IPv4 + IPv6 | **不要配 dedicated IPv4** |

> 為什麼不用 SQLite：防止重複預約依賴 PostgreSQL 的 exclusion constraint（`btree_gist`），排程依賴 `select_for_update(skip_locked=True)`，SQLite 都不支援。

> 沒有常駐 `qcluster`：`TASKS_SYNC=True` 讓推播在請求內直接送出；發送失敗不會影響後台操作，會記錄在後台「發送失敗的推播」，由每小時的排程重試（24 小時內）。

部署前請先用 [Fly 價格計算機](https://fly.io/calculator) 確認費用。

## 1. 準備外部 PostgreSQL（Neon）

1. 到 [neon.tech](https://neon.tech) 建立專案，區域選 **AWS Asia Pacific (Singapore)**，與 `fly.toml` 的 `primary_region = "sin"` 相同。
2. 複製連線字串，使用**直連**（不是 `-pooler` 的那個 host）：
   `postgres://user:password@ep-xxx.ap-southeast-1.aws.neon.tech/neondb?sslmode=require`
   - pooler（PgBouncer transaction mode）與 Django 的部分功能不相容，流量小也用不到。

## 2. 建立 Fly app

```bash
fly auth login
fly apps create <你的 app 名稱>
```

修改 `fly.toml`：
- `app` 改成你的 app 名稱
- `DJANGO_ALLOWED_HOSTS` 改成 `<app 名稱>.fly.dev`（之後綁自訂網域再加上）

## 3. 設定 secrets

```bash
fly secrets set --stage \
  DJANGO_SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(50))')" \
  DATABASE_URL="postgres://...?sslmode=require" \
  LIFF_ID="..." \
  LINE_CHANNEL_ID="..." \
  LINE_MESSAGING_CHANNEL_SECRET="..." \
  LINE_MESSAGING_CHANNEL_ACCESS_TOKEN="..." \
  LINE_OA_BASIC_ID="@..."
```

`--stage` 讓 secrets 在下次部署時一起套用。secrets 會注入所有機器（包含排程機器）。

## 4. 部署

```bash
scripts/fly-deploy.sh
```

腳本會：
1. `fly deploy --ha=false`：只開 1 台 web（預設會開 2 台），部署前自動跑 migration（`release_command`）
2. 建立或更新名為 `cron` 的排程機器，使用同一個 image（`fly deploy` 不會更新它，所以一律用這個腳本部署）
3. 列出 IP，確認沒有 dedicated IPv4

第一次部署後建立後台帳號：

```bash
fly ssh console -C "python manage.py createsuperuser"
```

## 5. 部署後檢查

```bash
fly ips list          # 只能有 shared IPv4 與 IPv6；若出現 dedicated v4：fly ips release <IP>
fly scale show        # web 只有 1 台、256MB
fly machine list      # 一台 web（app）、一台 cron（schedule: hourly）
fly logs              # 出現 "Out of memory" → fly.toml 的 memory 改 "512mb" 再部署
```

排程機器第一次執行後（建立後約 1 小時內），用 `fly logs -i <cron 機器 ID>` 確認輸出：
```
未確認自動取消：0
預約提醒：0
重試失敗的推播：0
```

## 6. 更新 LINE 平台設定

- Messaging API → Webhook URL：`https://<app>.fly.dev/line/webhook/`，按 Verify
- MINI App channel → LIFF Endpoint URL：`https://<app>.fly.dev/liff/`
- 重新建立 Rich Menu（LIFF URL 不變的話可略過）：`fly ssh console -C "python manage.py setup_richmenu"`

## 已知取捨

- **冷啟動**：機器停止後的第一個請求要等喚醒（suspend 約數百毫秒，stop 數秒），Neon 閒置後喚醒也要約 0.5 秒。LINE webhook 若偶爾逾時，可在 Messaging API 設定開啟「Webhook redelivery」。
- **排程精度為每小時**：提醒在開始前 23～24 小時之間送出；未確認的預約在開始前 2～3 小時之間取消。顧客最早可預約時間保留了 1 小時給店家確認，仍然成立。
- **排程時間點**：Fly 的 hourly 是從機器建立時間起算，不是整點。
