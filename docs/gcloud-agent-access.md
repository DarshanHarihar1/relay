# Google Cloud access for Relay coding agents

Relay uses Google Cloud application-default credentials (ADC) and service identities. It does not use a reusable Gemini or Google API key for coding tools, deployed services, or browser clients.

## Locations and project boundary

- Project: `massive-dynamo-302008`
- Cloud Run: `asia-south1` (Mumbai)
- Firestore Native: `asia-south1` (Mumbai)
- Vertex AI: `us-central1`

The default Firestore location is durable. The existing default database must remain Firestore Native in `asia-south1`. On a new project, pass `--firestore-location asia-south1` only after the user explicitly approves that location. Firebase initialization follows this decision, preventing conflicting default-resource locations.

## Local setup

The following local paths contain tooling or credentials and are ignored by Git:

- `work/google-cloud-cli/`
- `work/gcloud-config/`
- `.gcloud/`
- application-default credential and service-account key filename patterns

From the Relay workspace, export the local configuration. Do not put credential values in `.env`.

```bash
export RELAY_GCLOUD_BIN="$PWD/work/google-cloud-cli/google-cloud-sdk/bin/gcloud"
export CLOUDSDK_CONFIG="$PWD/work/gcloud-config"
export GOOGLE_CLOUD_PROJECT="massive-dynamo-302008"
export GOOGLE_CLOUD_LOCATION="us-central1"
export GOOGLE_GENAI_USE_VERTEXAI="true"
```

On a developer machine only, sign in and create local ADC:

```bash
"$RELAY_GCLOUD_BIN" auth login --update-adc --project="$GOOGLE_CLOUD_PROJECT"
```

Never run that command in CI, a container image, or a deployed Cloud Run revision. Never create service-account key files.

After `relay-dev-agent` exists, use short-lived impersonation rather than the bootstrap Owner identity:

```bash
"$RELAY_GCLOUD_BIN" config set auth/impersonate_service_account \
  "relay-dev-agent@${GOOGLE_CLOUD_PROJECT}.iam.gserviceaccount.com"
"$RELAY_GCLOUD_BIN" auth application-default login \
  --impersonate-service-account="relay-dev-agent@${GOOGLE_CLOUD_PROJECT}.iam.gserviceaccount.com" \
  --project="$GOOGLE_CLOUD_PROJECT"
```

The developer account receives `roles/iam.serviceAccountTokenCreator` only on `relay-dev-agent`. Revoke local access with `gcloud auth revoke`, remove `work/gcloud-config/`, and remove that Token Creator binding.

## Bootstrap and verification

Review the plan before allowing any mutation:

```bash
infra/gcp/bootstrap.sh --project massive-dynamo-302008 --region asia-south1 --dry-run
```

The bootstrap has a fixed MVP API list and never discovers or enables APIs from application code. It creates these identities only if missing:

- `relay-dev-agent` for human-supervised local coding and diagnostics
- `relay-api` for the Cloud Run API
- `relay-worker` for the Cloud Run worker
- `relay-deployer` for CI deployment

Runtime identities have only their stated datastore, Secret Manager, logging, monitoring, and later resource-specific Pub/Sub permissions. `relay-deployer` can deploy Cloud Run and use the two runtime identities, but does not receive Gmail, People, secret, or Firestore data access. The browser receives no Cloud credentials; Firebase browser configuration is public configuration, not a service credential.

After an approved bootstrap, verify non-mutating access:

```bash
infra/gcp/verify-agent-access.sh
```

The verifier checks ADC without printing an access token, reports missing baseline APIs without enabling them, redacts unsafe identity output, and fails if local credential paths are tracked.

## Key rotation

Any previously pasted Google API key is treated as compromised. Rotate it before using Google Maps, Gemini Developer API, or browser configuration. Do not place its replacement in this repository.
