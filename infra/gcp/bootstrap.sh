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

readonly REQUIRED_APIS=(
  run.googleapis.com
  firestore.googleapis.com
  pubsub.googleapis.com
  secretmanager.googleapis.com
  artifactregistry.googleapis.com
  cloudbuild.googleapis.com
  gmail.googleapis.com
  calendar-json.googleapis.com
  people.googleapis.com
  routes.googleapis.com
  places-backend.googleapis.com
  firebase.googleapis.com
  fcm.googleapis.com
  logging.googleapis.com
  clouderrorreporting.googleapis.com
)

readonly API_SERVICE_ACCOUNT="relay-api-sa@${project_id}.iam.gserviceaccount.com"
readonly WORKER_SERVICE_ACCOUNT="relay-worker-sa@${project_id}.iam.gserviceaccount.com"

ensure_service_account() {
  local account_id=$1
  local account_email="${account_id}@${project_id}.iam.gserviceaccount.com"

  if ! gcloud iam service-accounts describe "$account_email" --project="$project_id" >/dev/null 2>&1; then
    gcloud iam service-accounts create "$account_id" \
      --project="$project_id" \
      --display-name="Relay ${account_id}"
  fi
}

grant_project_role() {
  local service_account=$1
  local role=$2
  gcloud projects add-iam-policy-binding "$project_id" \
    --member="serviceAccount:${service_account}" \
    --role="$role" \
    --quiet >/dev/null
}

grant_secret_access() {
  local service_account=$1
  local secret_name=$2
  if gcloud secrets describe "$secret_name" --project="$project_id" >/dev/null 2>&1; then
    gcloud secrets add-iam-policy-binding "$secret_name" \
      --project="$project_id" \
      --member="serviceAccount:${service_account}" \
      --role='roles/secretmanager.secretAccessor' \
      --quiet >/dev/null
  else
    printf 'Secret %s is not present yet; rerun bootstrap after approved secret injection.\n' "$secret_name" >&2
  fi
}

gcloud services enable "${REQUIRED_APIS[@]}" --project="$project_id"

ensure_service_account relay-api-sa
ensure_service_account relay-worker-sa

for service_account in "$API_SERVICE_ACCOUNT" "$WORKER_SERVICE_ACCOUNT"; do
  grant_project_role "$service_account" roles/datastore.user
  grant_project_role "$service_account" roles/logging.logWriter
  grant_project_role "$service_account" roles/errorreporting.writer
done

for secret_name in GEMINI_API_KEY GOOGLE_OAUTH_CLIENT_SECRET MAPS_API_KEY APP_ENCRYPTION_KEY; do
  grant_secret_access "$API_SERVICE_ACCOUNT" "$secret_name"
done

for secret_name in VAPI_PRIVATE_KEY VAPI_WEBHOOK_SECRET TWILIO_ACCOUNT_SID TWILIO_AUTH_TOKEN TWILIO_PHONE_NUMBER APP_ENCRYPTION_KEY; do
  grant_secret_access "$WORKER_SERVICE_ACCOUNT" "$secret_name"
done

printf 'Relay foundation ready for project %s in region %s.\n' "$project_id" "$region"
