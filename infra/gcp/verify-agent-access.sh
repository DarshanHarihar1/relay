#!/usr/bin/env bash

set -euo pipefail

readonly EXPECTED_PROJECT='massive-dynamo-302008'
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

allow_project_override=false
if [[ ${1:-} == '--allow-project-override' ]]; then
  allow_project_override=true
  shift
fi
if (( $# > 0 )); then
  printf 'Usage: infra/gcp/verify-agent-access.sh [--allow-project-override]\n' >&2
  exit 2
fi

for required_var in RELAY_GCLOUD_BIN CLOUDSDK_CONFIG GOOGLE_CLOUD_PROJECT; do
  [[ -n ${!required_var:-} ]] || { printf '%s must be set.\n' "$required_var" >&2; exit 2; }
done

if [[ "$allow_project_override" != true && "$GOOGLE_CLOUD_PROJECT" != "$EXPECTED_PROJECT" ]]; then
  printf 'Refusing project %s; expected %s. Use --allow-project-override to proceed.\n' "$GOOGLE_CLOUD_PROJECT" "$EXPECTED_PROJECT" >&2
  exit 2
fi

[[ -x "$RELAY_GCLOUD_BIN" ]] || { printf 'RELAY_GCLOUD_BIN is not executable.\n' >&2; exit 2; }

configured_project=$("$RELAY_GCLOUD_BIN" config get-value project 2>/dev/null || true)
if [[ "$configured_project" != "$GOOGLE_CLOUD_PROJECT" ]]; then
  printf 'Configured gcloud project does not match GOOGLE_CLOUD_PROJECT.\n' >&2
  exit 1
fi

safe_identity() {
  local identity=$1
  if [[ "$identity" =~ ^[A-Za-z0-9._%+@-]+$ ]]; then
    printf '%s' "$identity"
  else
    printf '<redacted>'
  fi
}

active_account=$("$RELAY_GCLOUD_BIN" auth list --filter=status:ACTIVE --format='value(account)' 2>/dev/null || true)
impersonated_account=$("$RELAY_GCLOUD_BIN" config get-value auth/impersonate_service_account 2>/dev/null || true)
printf 'Active account (reviewed): %s\n' "$(safe_identity "${active_account:-<none>}")"
printf 'Impersonated service account (reviewed): %s\n' "$(safe_identity "${impersonated_account:-<none>}")"

if "$RELAY_GCLOUD_BIN" auth application-default print-access-token >/dev/null 2>&1; then
  printf 'Application-default credentials: valid\n'
else
  printf 'Application-default credentials: unavailable\n' >&2
  exit 1
fi

enabled_apis=$("$RELAY_GCLOUD_BIN" services list --enabled --project="$GOOGLE_CLOUD_PROJECT" --format='value(config.name)')
missing_apis=0
for api in "${REQUIRED_APIS[@]}"; do
  if ! grep -Fqx "$api" <<<"$enabled_apis"; then
    printf 'Missing required API: %s\n' "$api" >&2
    missing_apis=1
  fi
done

tracked_credentials=$(git ls-files -- \
  ':(glob)work/google-cloud-cli/**' \
  ':(glob)work/gcloud-config/**' \
  ':(glob).gcloud/**' \
  'application_default_credentials.json' \
  ':(glob)**/*.service-account.json' \
  ':(glob)**/*.key.json')
if [[ -n "$tracked_credentials" ]]; then
  printf 'Tracked credential path detected; remove it from Git:\n%s\n' "$tracked_credentials" >&2
  exit 1
fi

if (( missing_apis != 0 )); then
  exit 1
fi

printf 'Required APIs: enabled\n'
printf 'Credential paths: not tracked\n'
