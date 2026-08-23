#!/usr/bin/env bash

set -euo pipefail

# The one project this script may touch without an explicit override.
readonly EXPECTED_PROJECT='massive-dynamo-302008'

usage() {
  cat >&2 <<'USAGE'
Usage: bootstrap.sh --project PROJECT_ID --region REGION [--dry-run] [--allow-project-override]

  --project                  Target project. Defaults to $GOOGLE_CLOUD_PROJECT.
  --region                   Cloud Run region. Defaults to $GOOGLE_CLOUD_REGION.
  --dry-run                  Print the plan and exit. Makes no change of any kind.
  --allow-project-override   Permit a project other than the expected one.
USAGE
}

readonly REQUIRED_APIS=(
  aiplatform.googleapis.com
  run.googleapis.com
  firestore.googleapis.com
  pubsub.googleapis.com
  secretmanager.googleapis.com
  artifactregistry.googleapis.com
  cloudbuild.googleapis.com
  gmail.googleapis.com
  people.googleapis.com
  calendar-json.googleapis.com
  routes.googleapis.com
  places-backend.googleapis.com
  firebase.googleapis.com
  identitytoolkit.googleapis.com
  fcm.googleapis.com
  logging.googleapis.com
  clouderrorreporting.googleapis.com
  cloudscheduler.googleapis.com
)

# Runtime roles are identical for both services; Firestore has no per-collection
# IAM, so roles/datastore.user is the narrowest binding that works.
readonly RUNTIME_ROLES=(
  roles/datastore.user
  roles/logging.logWriter
  roles/errorreporting.writer
)

readonly DEPLOYER_ROLES=(
  roles/run.admin
  roles/artifactregistry.writer
)

readonly API_SECRETS=(
  GOOGLE_OAUTH_CLIENT_SECRET GOOGLE_OAUTH_STATE_SIGNING_KEY MAPS_API_KEY APP_ENCRYPTION_KEY
)
readonly WORKER_SECRETS=(
  VAPI_PRIVATE_KEY VAPI_WEBHOOK_SECRET TWILIO_ACCOUNT_SID TWILIO_AUTH_TOKEN
  TWILIO_PHONE_NUMBER APP_ENCRYPTION_KEY
)

project_id=${GOOGLE_CLOUD_PROJECT:-}
region=${GOOGLE_CLOUD_REGION:-}
dry_run=false
allow_project_override=false

while (( $# > 0 )); do
  case $1 in
    --project) project_id=${2:-}; shift 2 ;;
    --project=*) project_id=${1#*=}; shift ;;
    --region) region=${2:-}; shift 2 ;;
    --region=*) region=${1#*=}; shift ;;
    --dry-run) dry_run=true; shift ;;
    --allow-project-override) allow_project_override=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Unknown argument: %s\n' "$1" >&2; usage; exit 2 ;;
  esac
done

if [[ -z "$project_id" || -z "$region" ]]; then
  usage
  exit 2
fi

if [[ "$allow_project_override" != true && "$project_id" != "$EXPECTED_PROJECT" ]]; then
  printf 'Refusing project %s; expected %s. Use --allow-project-override to proceed.\n' \
    "$project_id" "$EXPECTED_PROJECT" >&2
  exit 2
fi

readonly DEV_AGENT="relay-dev-agent@${project_id}.iam.gserviceaccount.com"
readonly API_SERVICE_ACCOUNT="relay-api-sa@${project_id}.iam.gserviceaccount.com"
readonly WORKER_SERVICE_ACCOUNT="relay-worker-sa@${project_id}.iam.gserviceaccount.com"
readonly DEPLOYER="relay-deployer@${project_id}.iam.gserviceaccount.com"

# Never resolved during a dry run, so the plan works without gcloud on PATH.
active_principal() {
  gcloud config get-value account 2>/dev/null || printf '<active gcloud account>'
}

print_plan() {
  printf 'Relay bootstrap plan for project %s in region %s.\n\n' "$project_id" "$region"

  printf 'APIs to enable (idempotent):\n'
  printf '  %s\n' "${REQUIRED_APIS[@]}"

  printf '\nService accounts to create if absent:\n'
  printf '  relay-dev-agent   supervised local diagnostics\n'
  printf '  relay-api-sa      Cloud Run API runtime\n'
  printf '  relay-worker-sa   asynchronous worker runtime\n'
  printf '  relay-deployer    CI deployment\n'

  printf '\nProposed bindings:\n'
  printf '  relay-dev-agent -> roles/viewer\n'
  for role in "${RUNTIME_ROLES[@]}"; do
    printf '  relay-api-sa -> %s\n' "$role"
    printf '  relay-worker-sa -> %s\n' "$role"
  done
  for role in "${DEPLOYER_ROLES[@]}"; do
    printf '  relay-deployer -> %s\n' "$role"
  done
  printf '  relay-deployer -> relay-api-sa (roles/iam.serviceAccountUser)\n'
  printf '  relay-deployer -> relay-worker-sa (roles/iam.serviceAccountUser)\n'
  printf '  active bootstrap principal -> relay-dev-agent (roles/iam.serviceAccountTokenCreator)\n'

  printf '\nSecret accessor bindings (skipped for any secret that does not exist yet):\n'
  printf '  relay-api-sa -> %s\n' "${API_SECRETS[@]}"
  printf '  relay-worker-sa -> %s\n' "${WORKER_SECRETS[@]}"

  printf '\nThis script never creates or downloads a service-account key.\n'
}

if [[ "$dry_run" == true ]]; then
  print_plan
  printf '\nDry run only. No change was made.\n'
  exit 0
fi

ensure_service_account() {
  local account_id=$1
  local description=$2
  local account_email="${account_id}@${project_id}.iam.gserviceaccount.com"

  if ! gcloud iam service-accounts describe "$account_email" --project="$project_id" >/dev/null 2>&1; then
    gcloud iam service-accounts create "$account_id" \
      --project="$project_id" \
      --display-name="Relay ${description}" \
      --quiet >/dev/null
  fi
}

grant_project_role() {
  gcloud projects add-iam-policy-binding "$project_id" \
    --member="serviceAccount:$1" \
    --role="$2" \
    --quiet >/dev/null
}

grant_act_as() {
  gcloud iam service-accounts add-iam-policy-binding "$2" \
    --project="$project_id" \
    --member="serviceAccount:$1" \
    --role='roles/iam.serviceAccountUser' \
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
    printf 'Secret %s is not present yet; rerun bootstrap after approved secret injection.\n' \
      "$secret_name" >&2
  fi
}

print_plan
printf '\nApplying.\n'

gcloud services enable "${REQUIRED_APIS[@]}" --project="$project_id"

ensure_service_account relay-dev-agent 'development agent'
ensure_service_account relay-api-sa 'API runtime'
ensure_service_account relay-worker-sa 'worker runtime'
ensure_service_account relay-deployer 'deployer'

grant_project_role "$DEV_AGENT" roles/viewer
for service_account in "$API_SERVICE_ACCOUNT" "$WORKER_SERVICE_ACCOUNT"; do
  for role in "${RUNTIME_ROLES[@]}"; do
    grant_project_role "$service_account" "$role"
  done
done
for role in "${DEPLOYER_ROLES[@]}"; do
  grant_project_role "$DEPLOYER" "$role"
done

grant_act_as "$DEPLOYER" "$API_SERVICE_ACCOUNT"
grant_act_as "$DEPLOYER" "$WORKER_SERVICE_ACCOUNT"

# The human running bootstrap may then impersonate the development agent.
bootstrap_principal=$(active_principal)
if [[ -n "$bootstrap_principal" && "$bootstrap_principal" != '<active gcloud account>' ]]; then
  gcloud iam service-accounts add-iam-policy-binding "$DEV_AGENT" \
    --project="$project_id" \
    --member="user:${bootstrap_principal}" \
    --role='roles/iam.serviceAccountTokenCreator' \
    --quiet >/dev/null
fi

for secret_name in "${API_SECRETS[@]}"; do
  grant_secret_access "$API_SERVICE_ACCOUNT" "$secret_name"
done
for secret_name in "${WORKER_SECRETS[@]}"; do
  grant_secret_access "$WORKER_SERVICE_ACCOUNT" "$secret_name"
done

printf 'Relay foundation ready for project %s in region %s.\n' "$project_id" "$region"
