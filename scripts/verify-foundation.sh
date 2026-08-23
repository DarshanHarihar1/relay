#!/usr/bin/env bash
set -euo pipefail

pnpm check:contracts
pnpm lint
pnpm test:web

(
  cd services/relay-api
  uv run pytest -m "not emulator"
)

firestore_host="${FIRESTORE_EMULATOR_HOST:-}"
if [[ -z "$firestore_host" ]]; then
  echo "FIRESTORE_EMULATOR_HOST is required. Start it with: gcloud beta emulators firestore start --host-port=127.0.0.1:8080" >&2
  exit 1
fi

firestore_hostname="${firestore_host%:*}"
firestore_port="${firestore_host##*:}"
if [[ "$firestore_hostname" == "$firestore_host" || ! "$firestore_port" =~ ^[0-9]+$ ]] || ! nc -z "$firestore_hostname" "$firestore_port"; then
  echo "Firestore emulator is unavailable at $firestore_host. Start it with: gcloud beta emulators firestore start --host-port=127.0.0.1:8080" >&2
  exit 1
fi

(
  cd services/relay-api
  FIRESTORE_EMULATOR_HOST="$firestore_host" uv run pytest -m emulator
)
