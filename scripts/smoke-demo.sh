#!/usr/bin/env bash
set -euo pipefail

origin="${RELAY_API_ORIGIN:?RELAY_API_ORIGIN must be set}"
expected_plan="${RELAY_EXPECTED_REPAIR_PLAN_ID:-plan-demo}"
action_id="${RELAY_SMOKE_ACTION_ID:-}"
token="$(cat)"
[[ -n "$token" ]] || { echo "Firebase ID token is required on stdin" >&2; exit 2; }

tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT

correlation_id="relay-smoke-$(date +%s)"
health_body="$tmp_dir/health.json"
health_headers="$tmp_dir/health.headers"
dashboard_body="$tmp_dir/dashboard.json"
dashboard_headers="$tmp_dir/dashboard.headers"

curl --fail --silent --show-error --dump-header "$health_headers" \
  --output "$health_body" "$origin/healthz"
grep -qi '^x-correlation-id:' "$health_headers"
grep -Eq '"status"[[:space:]]*:[[:space:]]*"ok"' "$health_body"

curl --fail --silent --show-error --dump-header "$dashboard_headers" \
  --header "Authorization: Bearer $token" \
  --header "X-Correlation-ID: $correlation_id" \
  --output "$dashboard_body" "$origin/v1/dashboard"
grep -qi '^x-correlation-id:' "$dashboard_headers"
grep -Eq '"repair_plan_id"[[:space:]]*:[[:space:]]*"'"$expected_plan"'"' "$dashboard_body"

forbidden='provider_ref|phone_number|booking_reference|access_token|refresh_token|transcript|recording|message_body|enc:v1:'
if grep -Eiq "$forbidden" "$dashboard_body"; then
  echo "Smoke response contained a forbidden private field" >&2
  exit 1
fi

if [[ -n "$action_id" ]]; then
  audit_body="$tmp_dir/audit.json"
  curl --fail --silent --show-error \
    --header "Authorization: Bearer $token" \
    --header "X-Correlation-ID: $correlation_id-audit" \
    --output "$audit_body" "$origin/v1/actions/$action_id/audit"
  if grep -Eiq "$forbidden" "$audit_body"; then
    echo "Smoke audit response contained a forbidden private field" >&2
    exit 1
  fi
fi

echo "Relay smoke checks passed for correlation $correlation_id"
