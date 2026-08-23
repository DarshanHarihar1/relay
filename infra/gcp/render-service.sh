#!/usr/bin/env bash

set -euo pipefail

# Renders a Cloud Run service template for deployment. The template stays the
# reviewed source of truth; this only substitutes deployment-specific values.

usage() {
  printf 'Usage: %s --service NAME --project ID --region REGION [--service-url URL] [--gmail-label ID] [--tag TAG]\n' \
    "${0##*/}" >&2
}

service='' project='' region='' service_url='' gmail_label='' tag='v1'
while (( $# > 0 )); do
  case $1 in
    --service) service=${2:-}; shift 2 ;;
    --project) project=${2:-}; shift 2 ;;
    --region) region=${2:-}; shift 2 ;;
    --service-url) service_url=${2:-}; shift 2 ;;
    --gmail-label) gmail_label=${2:-}; shift 2 ;;
    --tag) tag=${2:-}; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Unknown argument: %s\n' "$1" >&2; usage; exit 2 ;;
  esac
done
[[ -n "$service" && -n "$project" && -n "$region" ]] || { usage; exit 2; }

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
template="${repo_root}/infra/gcp/cloudrun/${service}.service.yaml"
[[ -f "$template" ]] || { printf 'No template at %s\n' "$template" >&2; exit 1; }

# Before the first deploy the service URL is unknown; a placeholder keeps the
# document valid and the redeploy fills it in.
[[ -n "$service_url" ]] || service_url="https://${service}-PENDING.${region}.run.app"
[[ -n "$gmail_label" ]] || gmail_label='Label_REPLACE_ME'

sed \
  -e "s|REGION-docker.pkg.dev|${region}-docker.pkg.dev|g" \
  -e "s|relay/${service}:latest|relay/${service}:${tag}|g" \
  -e "s|PROJECT_ID|${project}|g" \
  -e "s|https://SERVICE_URL|${service_url}|g" \
  -e "s|Label_REPLACE_ME|${gmail_label}|g" \
  "$template"
