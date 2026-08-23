#!/usr/bin/env bash

set -euo pipefail

usage() {
  printf 'Usage: %s PROJECT_ID REGION\n' "${0##*/}" >&2
}

project_id=${1:-}
region=${2:-}
if [[ -z "$project_id" || -z "$region" || $# -ne 2 ]]; then
  usage
  exit 2
fi

readonly API_SERVICE_ACCOUNT="relay-api-sa@${project_id}.iam.gserviceaccount.com"
readonly WORKER_SERVICE_ACCOUNT="relay-worker-sa@${project_id}.iam.gserviceaccount.com"
readonly PROJECT_NUMBER="$(gcloud projects describe "$project_id" --format='value(projectNumber)')"
readonly PUBSUB_SERVICE_AGENT="service-${PROJECT_NUMBER}@gcp-sa-pubsub.iam.gserviceaccount.com"
readonly DEAD_LETTER_TOPIC='relay-dead-letter'
readonly RETRY_POLICY='--min-retry-delay=10s --max-retry-delay=600s'

for topic in gmail-events relay-work relay-action-work relay-retry "$DEAD_LETTER_TOPIC"; do
  if ! gcloud pubsub topics describe "$topic" --project="$project_id" >/dev/null 2>&1; then
    gcloud pubsub topics create "$topic" --project="$project_id" --quiet
  fi
done

gcloud pubsub topics add-iam-policy-binding gmail-events \
  --project="$project_id" \
  --member='serviceAccount:gmail-api-push@system.gserviceaccount.com' \
  --role='roles/pubsub.publisher' \
  --quiet >/dev/null

gcloud pubsub topics add-iam-policy-binding relay-work \
  --project="$project_id" \
  --member="serviceAccount:${API_SERVICE_ACCOUNT}" \
  --role='roles/pubsub.publisher' \
  --quiet >/dev/null

gcloud pubsub topics add-iam-policy-binding relay-action-work \
  --project="$project_id" \
  --member="serviceAccount:${API_SERVICE_ACCOUNT}" \
  --role='roles/pubsub.publisher' \
  --quiet >/dev/null

gcloud pubsub topics add-iam-policy-binding "$DEAD_LETTER_TOPIC" \
  --project="$project_id" \
  --member="serviceAccount:${PUBSUB_SERVICE_AGENT}" \
  --role='roles/pubsub.publisher' \
  --quiet >/dev/null

gcloud pubsub topics add-iam-policy-binding "$DEAD_LETTER_TOPIC" \
  --project="$project_id" \
  --member="serviceAccount:${PUBSUB_SERVICE_AGENT}" \
  --role='roles/pubsub.subscriber' \
  --quiet >/dev/null

gcloud iam service-accounts add-iam-policy-binding "$WORKER_SERVICE_ACCOUNT" \
  --project="$project_id" \
  --member="serviceAccount:${PUBSUB_SERVICE_AGENT}" \
  --role='roles/iam.serviceAccountTokenCreator' \
  --quiet >/dev/null

gcloud iam service-accounts add-iam-policy-binding "$API_SERVICE_ACCOUNT" \
  --project="$project_id" \
  --member="serviceAccount:${PUBSUB_SERVICE_AGENT}" \
  --role='roles/iam.serviceAccountTokenCreator' \
  --quiet >/dev/null

ensure_processing_subscription() {
  local subscription=$1
  local topic=$2
  gcloud pubsub subscriptions create "$subscription" \
    --project="$project_id" \
    --topic="$topic" \
    --dead-letter-topic="$DEAD_LETTER_TOPIC" \
    --max-delivery-attempts=5 \
    $RETRY_POLICY \
    --quiet 2>/dev/null || gcloud pubsub subscriptions update "$subscription" \
      --project="$project_id" \
      --dead-letter-topic="$DEAD_LETTER_TOPIC" \
      --max-delivery-attempts=5 \
      $RETRY_POLICY \
      --quiet
}

ensure_processing_subscription gmail-events-api gmail-events
ensure_processing_subscription relay-work-worker relay-work
ensure_processing_subscription relay-action-work-worker relay-action-work
ensure_processing_subscription relay-retry-worker relay-retry

if ! gcloud pubsub subscriptions describe relay-dead-letter-operator --project="$project_id" >/dev/null 2>&1; then
  gcloud pubsub subscriptions create relay-dead-letter-operator \
    --project="$project_id" \
    --topic="$DEAD_LETTER_TOPIC" \
    --quiet
fi

# Push delivery is applied after the Cloud Run services exist. The worker route
# is private and accepts only an OIDC token for relay-worker-sa.
configure_push_subscription() {
  local subscription=$1
  local service_name=$2
  local route=$3
  local invoker=$4
  local service_url
  service_url=$(gcloud run services describe "$service_name" \
    --project="$project_id" --region="$region" --format='value(status.url)' 2>/dev/null || true)
  [[ -n "$service_url" ]] || return 1

  gcloud pubsub subscriptions update "$subscription" \
    --project="$project_id" \
    --push-endpoint="${service_url}${route}" \
    --push-auth-service-account="$invoker" \
    --quiet
  gcloud run services add-iam-policy-binding "$service_name" \
    --project="$project_id" \
    --region="$region" \
    --member="serviceAccount:${invoker}" \
    --role='roles/run.invoker' \
    --quiet >/dev/null
}

if ! configure_push_subscription gmail-events-api relay-api /v1/events/gmail "$API_SERVICE_ACCOUNT"; then
  printf 'Deploy relay-api, then rerun this script to configure Gmail push delivery.\n' >&2
fi

if gcloud run services describe relay-worker --project="$project_id" --region="$region" >/dev/null 2>&1; then
  configure_push_subscription relay-work-worker relay-worker /internal/pubsub/relay-work "$WORKER_SERVICE_ACCOUNT"
  configure_push_subscription relay-action-work-worker relay-worker /internal/pubsub/relay-action-work "$WORKER_SERVICE_ACCOUNT"
  configure_push_subscription relay-retry-worker relay-worker /internal/pubsub/relay-retry "$WORKER_SERVICE_ACCOUNT"
else
  printf 'Deploy relay-worker, then rerun this script to configure its private push routes.\n' >&2
fi

# The daily cleanup and Gmail watch renewal run from Cloud Scheduler with an OIDC
# token for the same service account the push route already accepts.
configure_daily_maintenance() {
  local service_url
  service_url=$(gcloud run services describe relay-api \
    --project="$project_id" --region="$region" --format='value(status.url)' 2>/dev/null || true)
  [[ -n "$service_url" ]] || return 1

  gcloud scheduler jobs describe relay-daily-maintenance \
    --project="$project_id" --location="$region" >/dev/null 2>&1 \
    && gcloud scheduler jobs update http relay-daily-maintenance \
      --project="$project_id" \
      --location="$region" \
      --schedule='0 3 * * *' \
      --uri="${service_url}/internal/maintenance/daily" \
      --http-method=POST \
      --oidc-service-account-email="$API_SERVICE_ACCOUNT" \
      --oidc-token-audience="${service_url}/v1/events/gmail" \
      --quiet \
    || gcloud scheduler jobs create http relay-daily-maintenance \
      --project="$project_id" \
      --location="$region" \
      --schedule='0 3 * * *' \
      --uri="${service_url}/internal/maintenance/daily" \
      --http-method=POST \
      --oidc-service-account-email="$API_SERVICE_ACCOUNT" \
      --oidc-token-audience="${service_url}/v1/events/gmail" \
      --quiet
}

if ! configure_daily_maintenance; then
  printf 'Deploy relay-api, then rerun this script to schedule daily retention cleanup.\n' >&2
fi

printf 'Pub/Sub topics and processing subscriptions are configured for %s.\n' "$project_id"
