#!/usr/bin/env bash

set -euo pipefail

# Applies infra/gcp/firestore.indexes.json.
#
# gcloud cannot set COLLECTION_GROUP query scope on a single-field index, so the
# field overrides go through the Firestore Admin REST API with ADC. Composite
# indexes do go through gcloud.

usage() { printf 'Usage: %s --project PROJECT_ID [--dry-run]\n' "${0##*/}" >&2; }

project_id=${GOOGLE_CLOUD_PROJECT:-}
dry_run=false
while (( $# > 0 )); do
  case $1 in
    --project) project_id=${2:-}; shift 2 ;;
    --project=*) project_id=${1#*=}; shift ;;
    --dry-run) dry_run=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Unknown argument: %s\n' "$1" >&2; usage; exit 2 ;;
  esac
done
[[ -n "$project_id" ]] || { usage; exit 2; }

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
config="${repo_root}/infra/gcp/firestore.indexes.json"
[[ -f "$config" ]] || { printf 'Missing %s\n' "$config" >&2; exit 1; }

base="https://firestore.googleapis.com/v1/projects/${project_id}/databases/(default)"

apply_field_override() {
  local collection_group=$1 field_path=$2 body=$3
  local url="${base}/collectionGroups/${collection_group}/fields/${field_path}?updateMask=indexConfig"

  if [[ "$dry_run" == true ]]; then
    printf '  would PATCH %s.%s\n' "$collection_group" "$field_path"
    return 0
  fi

  local token status
  token=$(gcloud auth application-default print-access-token)
  status=$(curl -sS -o /tmp/relay-index-response.json -w '%{http_code}' -X PATCH "$url" \
    -H "Authorization: Bearer ${token}" \
    -H 'Content-Type: application/json' \
    -d "$body")
  if [[ "$status" == 2* ]]; then
    printf '  applied %s.%s\n' "$collection_group" "$field_path"
  else
    printf '  FAILED %s.%s (HTTP %s)\n' "$collection_group" "$field_path" "$status" >&2
    cat /tmp/relay-index-response.json >&2
    return 1
  fi
}

printf 'Field overrides (collection-group scope):\n'
while IFS=$'\t' read -r collection_group field_path body; do
  apply_field_override "$collection_group" "$field_path" "$body"
done < <(python3 -c '
import json, sys

# The file uses the Firebase CLI shape ({queryScope, order}); the Admin REST API
# wants each index to carry an explicit single-entry fields array.
config = json.load(open(sys.argv[1]))
for override in config.get("fieldOverrides", []):
    field_path = override["fieldPath"]
    indexes = [
        {
            "queryScope": index["queryScope"],
            "fields": [{"fieldPath": field_path, "order": index["order"]}],
        }
        for index in override["indexes"]
    ]
    body = json.dumps({"indexConfig": {"indexes": indexes}})
    print("\t".join([override["collectionGroup"], field_path, body]))
' "$config")

printf 'Composite indexes:\n'
while IFS=$'\t' read -r collection_group fields; do
  if [[ "$dry_run" == true ]]; then
    printf '  would create composite index on %s (%s)\n' "$collection_group" "$fields"
    continue
  fi
  # shellcheck disable=SC2086
  gcloud firestore indexes composite create \
    --project="$project_id" \
    --collection-group="$collection_group" \
    --query-scope=COLLECTION \
    $fields \
    --async \
    --quiet 2>/dev/null \
    && printf '  created composite index on %s\n' "$collection_group" \
    || printf '  composite index on %s already exists or is building\n' "$collection_group"
done < <(python3 -c '
import json, sys
config = json.load(open(sys.argv[1]))
for index in config.get("indexes", []):
    flags = " ".join(
        "--field-config=field-path=%s,order=%s" % (f["fieldPath"], f["order"].lower())
        for f in index["fields"]
    )
    print("\t".join([index["collectionGroup"], flags]))
' "$config")

printf 'Firestore index configuration applied for %s.\n' "$project_id"
