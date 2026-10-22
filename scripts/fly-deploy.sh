#!/usr/bin/env bash
# 部署 web，並把排程機器（每小時跑一次 run_scheduled_jobs）更新到同一個 image。
#
# fly deploy 不會更新用 fly machine run 建立的排程機器，不同步的話排程會一直跑舊程式。
# 用法：scripts/fly-deploy.sh
set -euo pipefail

APP=$(sed -nE 's/^app *= *"([^"]+)".*/\1/p' fly.toml)
REGION=$(sed -nE 's/^primary_region *= *"([^"]+)".*/\1/p' fly.toml)
LABEL="deploy-$(date +%Y%m%d%H%M%S)"
IMAGE="registry.fly.io/${APP}:${LABEL}"
CRON_NAME="cron"

echo "==> 部署 web（${IMAGE}）"
# --ha=false：只開 1 台 web 機器（預設會開 2 台做高可用，費用加倍）
fly deploy --app "$APP" --ha=false --image-label "$LABEL"

CRON_ID=$(fly machine list --app "$APP" --json | python3 -c "
import json, sys
for m in json.load(sys.stdin):
    if m.get('name') == '${CRON_NAME}':
        print(m['id'])
        break
")

# 排程機器設定：
# - --restart no：失敗也不要立刻重啟（否則會一直重跑），等下一個小時
# - TASKS_SYNC=True：fly.toml 的 [env] 不會套用到 fly machine run 建立的機器，要另外傳
# - secrets 會自動注入每台機器
CRON_FLAGS=(--schedule hourly --restart no --vm-size shared-cpu-1x --vm-memory 256 --env TASKS_SYNC=True)

if [[ -z "$CRON_ID" ]]; then
  echo "==> 建立排程機器"
  fly machine run "$IMAGE" python manage.py run_scheduled_jobs \
    --app "$APP" --name "$CRON_NAME" --region "$REGION" "${CRON_FLAGS[@]}"
else
  echo "==> 更新排程機器 ${CRON_ID}"
  fly machine update "$CRON_ID" --app "$APP" --image "$IMAGE" \
    --command "python manage.py run_scheduled_jobs" "${CRON_FLAGS[@]}" --yes
fi

echo "==> 完成。檢查 IP（應只有共享 IPv4 與 IPv6，不要有 dedicated IPv4）："
fly ips list --app "$APP"
