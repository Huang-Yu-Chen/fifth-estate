#!/usr/bin/env bash
# Trigger the GitHub workflows from Google Cloud Scheduler, because GitHub's own cron drops most
# scheduled runs. Two jobs (ingest hourly, publish every 3 hours) fit in the free tier (3 jobs per
# billing account).
#
# Needs a GitHub fine-grained personal access token limited to this repo with
# "Actions: Read and write". You type it in; it is never written to disk or echoed.
#
#   bash scripts/setup_cloud_scheduler.sh
set -euo pipefail

PROJECT="$(gcloud config get-value project 2>/dev/null)"
REGION="asia-east1"
REPO="Huang-Yu-Chen/fifth-estate"

echo "Google Cloud project: $PROJECT (region $REGION)"
gcloud services enable cloudscheduler.googleapis.com --project "$PROJECT"

read -rsp "Paste the GitHub fine-grained token (input hidden): " TOKEN
echo
[ -n "$TOKEN" ] || { echo "no token given"; exit 1; }

upsert() {
  local name="$1" workflow="$2" schedule="$3"
  local verb=create
  if gcloud scheduler jobs describe "$name" --project "$PROJECT" --location "$REGION" >/dev/null 2>&1; then
    verb=update
  fi
  gcloud scheduler jobs "$verb" http "$name" \
    --project "$PROJECT" --location "$REGION" \
    --schedule "$schedule" --time-zone "Asia/Taipei" \
    --uri "https://api.github.com/repos/$REPO/actions/workflows/$workflow/dispatches" \
    --http-method POST \
    --update-headers "Authorization=Bearer $TOKEN,Accept=application/vnd.github+json,User-Agent=fifth-estate-scheduler,Content-Type=application/json" \
    --message-body '{"ref":"main"}' \
    --attempt-deadline 30s \
    --max-retry-attempts 3 \
    --quiet >/dev/null
  echo "✓ $verb $name → $workflow ($schedule, Asia/Taipei)"
}

upsert fifth-estate-ingest ingest.yml "7 * * * *"
upsert fifth-estate-publish publish.yml "27 */3 * * *"
unset TOKEN

echo "Test now with: gcloud scheduler jobs run fifth-estate-ingest --project $PROJECT --location $REGION"
