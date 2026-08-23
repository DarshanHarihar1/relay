#!/usr/bin/env bash

set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
cd "$repo_root"

bootstrap='infra/gcp/bootstrap.sh'
ignore_file='.gitignore'
env_example='.env.example'
access_doc='docs/gcloud-agent-access.md'

required_apis=(
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

failures=0

fail() {
  printf 'FAIL: %s\n' "$1" >&2
  failures=$((failures + 1))
}

require_file() {
  [[ -f "$1" ]] || fail "required file is missing: $1"
}

require_file "$bootstrap"
require_file "$ignore_file"
require_file "$env_example"
require_file "$access_doc"

if [[ -f "$bootstrap" ]]; then
  for api in "${required_apis[@]}"; do
    grep -Fq "$api" "$bootstrap" || fail "bootstrap is missing required API: $api"
  done

  grep -Fq -- '--dry-run' "$bootstrap" || fail 'bootstrap does not support --dry-run'
  grep -Fq -- '--allow-project-override' "$bootstrap" || fail 'bootstrap lacks an explicit project override guard'
  grep -Fq 'GOOGLE_CLOUD_PROJECT' "$bootstrap" || fail 'bootstrap lacks the GOOGLE_CLOUD_PROJECT guard'

  if grep -En '(iam service-accounts keys create|--key-file([=[:space:]]|$)|"private_key"[[:space:]]*:)' "$bootstrap" >/dev/null; then
    fail 'bootstrap contains a service-account key creation mechanism'
  fi

  if dry_run_output=$("$bootstrap" --project massive-dynamo-302008 --region us-central1 --dry-run 2>&1); then
    grep -Fq 'Proposed bindings:' <<<"$dry_run_output" || fail 'dry run does not list proposed bindings'
    grep -Fq 'relay-dev-agent -> roles/viewer' <<<"$dry_run_output" || fail 'dry run omits the development agent binding'
    grep -Fq 'active bootstrap principal -> relay-dev-agent (roles/iam.serviceAccountTokenCreator)' <<<"$dry_run_output" || fail 'dry run omits the developer impersonation binding'
    grep -Fq 'relay-deployer -> relay-api-sa (roles/iam.serviceAccountUser)' <<<"$dry_run_output" || fail 'dry run omits the deployer service-account binding'
  else
    fail 'dry run failed before it could report the planned bindings'
  fi
fi

for ignored_path in \
  '.env' \
  'work/google-cloud-cli/' \
  'work/gcloud-config/' \
  '.gcloud/' \
  'application_default_credentials.json' \
  '*.service-account.json' \
  '*.key.json'; do
  if [[ -f "$ignore_file" ]] && ! grep -Fqx "$ignored_path" "$ignore_file"; then
    fail "missing ignore rule: $ignored_path"
  fi
done

if [[ -f "$env_example" ]] && ! grep -Fqx 'FIRESTORE_LOCATION=asia-south1' "$env_example"; then
  fail 'the environment example must record FIRESTORE_LOCATION=asia-south1'
fi

if [[ -f "$access_doc" ]]; then
  grep -Fq 'asia-south1' "$access_doc" || fail 'access documentation omits the approved Firestore location'
  grep -Fq 'us-central1' "$access_doc" || fail 'access documentation omits the Vertex AI location'
fi

key_prefix='AI''za'
while IFS= read -r -d '' tracked_file; do
  if grep -EIn "${key_prefix}[A-Za-z0-9_-]{20,}|-----BEGIN (RSA |EC )?PRIVATE KEY-----|\"private_key\"[[:space:]]*:" "$tracked_file" >/dev/null; then
    fail "tracked secret-like value found: $tracked_file"
  fi
done < <(git ls-files -z)

if (( failures > 0 )); then
  exit 1
fi

printf 'PASS: gcloud bootstrap safety documentation checks\n'
