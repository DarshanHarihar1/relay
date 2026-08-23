#!/usr/bin/env bash

set -euo pipefail

readonly EXPECTED_PROJECT='massive-dynamo-302008'
readonly APPROVED_FIRESTORE_LOCATION='asia-south1'
readonly SERVICE_ACCOUNTS=(relay-dev-agent relay-api relay-worker relay-deployer)
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
)

usage() {
  cat <<'EOF'
Usage: infra/gcp/bootstrap.sh --project PROJECT --region REGION [options]

Options:
  --dry-run                    Print the planned changes without writing to Google Cloud.
  --allow-project-override     Allow a project other than GOOGLE_CLOUD_PROJECT.
  --firestore-location REGION  Confirm the default Firestore Native location for creation.
  -h, --help                   Show this help message.
EOF
}

project=''
region=''
dry_run=false
allow_project_override=false
firestore_location=''

while (( $# > 0 )); do
  case "$1" in
    --project)
      project=${2:?--project requires a value}
      shift 2
      ;;
    --region)
      region=${2:?--region requires a value}
      shift 2
      ;;
    --dry-run)
      dry_run=true
      shift
      ;;
    --allow-project-override)
      allow_project_override=true
      shift
      ;;
    --firestore-location)
      firestore_location=${2:?--firestore-location requires a value}
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      printf 'Unknown option: %s\n' "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

[[ -n "$project" ]] || { printf '%s\n' '--project is required' >&2; exit 2; }
[[ -n "$region" ]] || { printf '%s\n' '--region is required' >&2; exit 2; }

configured_project=${GOOGLE_CLOUD_PROJECT:-$EXPECTED_PROJECT}
if [[ "$allow_project_override" != true && "$project" != "$configured_project" ]]; then
  printf 'Refusing project %s; GOOGLE_CLOUD_PROJECT is %s. Use --allow-project-override to proceed.\n' "$project" "$configured_project" >&2
  exit 2
fi

if [[ -n "$firestore_location" && "$firestore_location" != "$APPROVED_FIRESTORE_LOCATION" ]]; then
  printf 'Refusing Firestore location %s; the approved location is %s.\n' "$firestore_location" "$APPROVED_FIRESTORE_LOCATION" >&2
  exit 2
fi

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
gcloud_bin=${RELAY_GCLOUD_BIN:-"$repo_root/work/google-cloud-cli/google-cloud-sdk/bin/gcloud"}
active_account='<unavailable: gcloud is not configured>'
if [[ -x "$gcloud_bin" ]]; then
  active_account=$("$gcloud_bin" auth list --filter=status:ACTIVE --format='value(account)' 2>/dev/null || true)
  active_account=${active_account:-'<none>'}
fi

printf 'Target project: %s\n' "$project"
printf 'Cloud Run region: %s\n' "$region"
printf 'Vertex AI region: us-central1\n'
printf 'Firestore location: %s\n' "$APPROVED_FIRESTORE_LOCATION"
printf 'Active account: %s\n' "$active_account"
printf 'Required APIs:\n'
printf '  %s\n' "${REQUIRED_APIS[@]}"
printf 'Service accounts:\n'
for account_id in "${SERVICE_ACCOUNTS[@]}"; do
  printf '  %s@%s.iam.gserviceaccount.com\n' "$account_id" "$project"
done
printf 'Proposed bindings:\n'
for role in roles/viewer roles/logging.viewer roles/monitoring.viewer roles/aiplatform.user; do
  printf '  relay-dev-agent -> %s\n' "$role"
done
printf '  active bootstrap principal -> relay-dev-agent (%s)\n' 'roles/iam.serviceAccountTokenCreator'
for account_id in relay-api relay-worker; do
  for role in roles/datastore.user roles/secretmanager.secretAccessor roles/logging.logWriter roles/monitoring.metricWriter; do
    printf '  %s -> %s\n' "$account_id" "$role"
  done
done
for role in roles/run.admin roles/artifactregistry.writer; do
  printf '  relay-deployer -> %s\n' "$role"
done
for runtime_account in relay-api relay-worker; do
  printf '  relay-deployer -> %s (%s)\n' "$runtime_account" 'roles/iam.serviceAccountUser'
done

if [[ "$dry_run" == true ]]; then
  printf 'Dry run: no Google Cloud changes will be made.\n'
  printf 'Would enable the listed APIs and create missing service accounts and bindings.\n'
  printf 'Would verify the default Firestore database is Native in %s.\n' "$APPROVED_FIRESTORE_LOCATION"
  printf 'Would create a default Firestore database only when --firestore-location=%s is supplied.\n' "$APPROVED_FIRESTORE_LOCATION"
  exit 0
fi

[[ -x "$gcloud_bin" ]] || { printf 'RELAY_GCLOUD_BIN is not executable: %s\n' "$gcloud_bin" >&2; exit 2; }
[[ "$active_account" != '<unavailable: gcloud is not configured>' && "$active_account" != '<none>' ]] || {
  printf 'An active bootstrap account is required to grant developer impersonation access.\n' >&2
  exit 2
}

"$gcloud_bin" services enable "${REQUIRED_APIS[@]}" --project="$project"

ensure_service_account() {
  local account_id=$1
  local account_email="${account_id}@${project}.iam.gserviceaccount.com"

  if ! "$gcloud_bin" iam service-accounts describe "$account_email" --project="$project" >/dev/null 2>&1; then
    printf 'Creating service account: %s\n' "$account_email"
    "$gcloud_bin" iam service-accounts create "$account_id" --project="$project" --display-name="Relay ${account_id}"
  fi
}

bind_project_role() {
  local account_id=$1
  local role=$2
  local member="serviceAccount:${account_id}@${project}.iam.gserviceaccount.com"
  printf 'Ensuring project binding: %s -> %s\n' "$member" "$role"
  "$gcloud_bin" projects add-iam-policy-binding "$project" --member="$member" --role="$role" --quiet >/dev/null
}

for account_id in "${SERVICE_ACCOUNTS[@]}"; do
  ensure_service_account "$account_id"
done

for role in roles/viewer roles/logging.viewer roles/monitoring.viewer roles/aiplatform.user; do
  bind_project_role relay-dev-agent "$role"
done

dev_agent_email="relay-dev-agent@${project}.iam.gserviceaccount.com"
printf 'Ensuring service-account binding: %s -> %s (%s)\n' "$active_account" "$dev_agent_email" 'roles/iam.serviceAccountTokenCreator'
"$gcloud_bin" iam service-accounts add-iam-policy-binding "$dev_agent_email" \
  --member="user:${active_account}" \
  --role='roles/iam.serviceAccountTokenCreator' --quiet >/dev/null

for account_id in relay-api relay-worker; do
  for role in roles/datastore.user roles/secretmanager.secretAccessor roles/logging.logWriter roles/monitoring.metricWriter; do
    bind_project_role "$account_id" "$role"
  done
done

for role in roles/run.admin roles/artifactregistry.writer; do
  bind_project_role relay-deployer "$role"
done

for runtime_account in relay-api relay-worker; do
  runtime_email="${runtime_account}@${project}.iam.gserviceaccount.com"
  printf 'Ensuring service-account binding: relay-deployer -> %s (%s)\n' "$runtime_email" 'roles/iam.serviceAccountUser'
  "$gcloud_bin" iam service-accounts add-iam-policy-binding "$runtime_email" \
    --member="serviceAccount:relay-deployer@${project}.iam.gserviceaccount.com" \
    --role='roles/iam.serviceAccountUser' --quiet >/dev/null
done

database_description=$("$gcloud_bin" firestore databases describe --project="$project" --database='(default)' --format='value(locationId,type)' 2>/dev/null || true)
if [[ -n "$database_description" ]]; then
  database_location=${database_description%%$'\t'*}
  database_type=${database_description##*$'\t'}
  if [[ "$database_location" != "$APPROVED_FIRESTORE_LOCATION" || "$database_type" != 'FIRESTORE_NATIVE' ]]; then
    printf 'Default Firestore database must be Native in %s; found %s.\n' "$APPROVED_FIRESTORE_LOCATION" "$database_description" >&2
    exit 1
  fi
  printf 'Verified default Firestore database: Native in %s.\n' "$APPROVED_FIRESTORE_LOCATION"
elif [[ "$firestore_location" == "$APPROVED_FIRESTORE_LOCATION" ]]; then
  "$gcloud_bin" firestore databases create --project="$project" --location="$APPROVED_FIRESTORE_LOCATION" --type='firestore-native'
else
  printf 'No default Firestore database was found. Refusing creation until --firestore-location=%s confirms the approved location.\n' "$APPROVED_FIRESTORE_LOCATION" >&2
  exit 1
fi

printf 'Bootstrap complete. No service-account key files were created.\n'
