# Local development

## First run

From the Relay workspace, run:

```bash
corepack enable
pnpm install --frozen-lockfile
cd services/relay-api && uv sync --all-groups
cd ../..
cp .env.example .env.local
pnpm dev:web
```

Keep `.env.local` untracked. Never copy production values into it from Cloud Run.

## Local emulators

In separate terminals, start the Task 2 emulators before running emulator-backed tests:

```bash
gcloud beta emulators firestore start --host-port=127.0.0.1:8080
gcloud beta emulators pubsub start --host-port=127.0.0.1:8085
firebase emulators:start --only auth --project relay-local
```

The emulator hosts above are the matching defaults in `.env.example`; keep
`RELAY_ENV=local` while running against them.

## Foundation verification

With the Firestore emulator running, verify the same gates used in CI from the
Relay workspace:

```bash
export FIRESTORE_EMULATOR_HOST=127.0.0.1:8080
pnpm verify:foundation
```

The command checks generated contracts, web linting and tests, API unit tests,
then Firestore-emulator tests. It stops at the first failure and refuses to run
the emulator tests unless `FIRESTORE_EMULATOR_HOST` points to a reachable local
emulator; it never falls back to a cloud database.

The deployment manifests read secrets only from Secret Manager. Use the secret
names in `infra/gcp/secret-manifest.txt` with an approved injection process;
do not add their values to this repository or `.env.local`.

## Release-candidate checks

The declarative build is in `infra/gcp/cloudbuild.yaml`. It runs contract,
web, API, and deterministic E2E checks before publishing SHA-tagged API and
worker images. The API is exposed only through the configured load balancer;
the worker remains internal and Pub/Sub-authenticated.

For a deployed, signed-in smoke check, provide a short-lived Firebase ID token
on stdin and set the expected opaque seed marker:

```bash
export RELAY_API_ORIGIN="https://SERVICE_URL"
export RELAY_EXPECTED_REPAIR_PLAN_ID="plan-demo"
printf '%s' "$FIREBASE_ID_TOKEN" | scripts/smoke-demo.sh
```

The smoke script never approves an action or calls a provider. Record its
correlation ID and the API/worker revision IDs in
`docs/release-checklist.md`; do not record the token.

## Google Cloud configuration

Relay uses Vertex AI through application-default credentials. Do not commit a `.env` file, API key, access token, service-account JSON file, or local Google Cloud configuration.

Set these values in your shell from the Relay workspace:

```bash
export RELAY_GCLOUD_BIN="$PWD/work/google-cloud-cli/google-cloud-sdk/bin/gcloud"
export CLOUDSDK_CONFIG="$PWD/work/gcloud-config"
export GOOGLE_CLOUD_PROJECT="massive-dynamo-302008"
export GOOGLE_CLOUD_LOCATION="us-central1"
export GOOGLE_GENAI_USE_VERTEXAI="true"
```

## Phase 4 consented-call rehearsal

1. Confirm the recipient is a team-owned or pre-consented number and the action has an unexpired call contract.
2. Confirm the deployed public HTTPS callback URL and both Vapi and Twilio signatures with a fixture before placing a call.
3. Approve the batch once, observe exactly one Vapi provider reference and one Firestore action transition to dispatched.
4. Let the assistant state its identity and record a structured outcome. Do not give it payment or booking credentials.
5. Replay the same callback fixture and confirm provider_events remains one record.
6. Verify the Calendar event by read-back and open the Uber handoff only from the user-controlled button.
7. Capture redacted Firestore action/audit evidence for Phase 5, never provider secrets or phone numbers.

Use `FIRESTORE_LOCATION=asia-south1` for Relay. Cloud Run services run in `asia-south1`; Vertex AI remains in `us-central1`. See [Google Cloud agent access](gcloud-agent-access.md) for bootstrap, impersonation, and verification steps.
