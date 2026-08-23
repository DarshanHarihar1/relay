# Google Cloud setup for coding agents

This is a practical setup checklist for an agent working on Relay or a similar Google Cloud project. It covers local `gcloud` access, Application Default Credentials (ADC), API enablement, Firestore, service identities, and verification.

The commands below use the current Relay project as an example. Replace the project ID, regions, and service-account names when setting up another project.

## Safety rules

- Never put API keys, OAuth codes, access tokens, refresh tokens, or service-account JSON keys in the repository.
- Prefer ADC for local development and service-account impersonation for agent work.
- Use Secret Manager for runtime secrets. Do not paste secret values into shell commands that may be saved in history.
- Review every command that changes cloud state. Start with a dry run where the repository provides one.
- Do not grant `roles/owner` to an agent. Use the smallest role needed for the task.
- Keep the local gcloud configuration in an ignored directory, not in the repository root.

## 1. Prerequisites

You need:

1. A Google Cloud project with billing enabled.
2. The project ID, not only the display name.
3. A confirmed primary region. Some resources, especially Firestore, cannot be moved later.
4. A Google account that can perform the initial bootstrap, or an administrator who can do it for you.
5. A writable local directory for the CLI configuration and ADC files.

Check whether the CLI is already installed:

```bash
gcloud version
```

If it is not installed, use the [official Google Cloud CLI installation guide](https://cloud.google.com/sdk/docs/install). On macOS, Homebrew is usually the shortest path:

```bash
brew install --cask google-cloud-sdk
```

In a restricted coding-agent workspace, a project-local CLI install also works. Keep it under an ignored path such as `work/google-cloud-cli/` and set `RELAY_GCLOUD_BIN` to its executable.

## 2. Configure project and regions

Run this from the repository root. `GOOGLE_CLOUD_REGION` is used for regional resources such as Cloud Run and Firestore. `GOOGLE_CLOUD_LOCATION` is the Vertex AI model location and can be different.

```bash
export GOOGLE_CLOUD_PROJECT="massive-dynamo-302008"
export GOOGLE_CLOUD_REGION="asia-south1"
export GOOGLE_CLOUD_LOCATION="us-central1"
export CLOUDSDK_CONFIG="$(cd .. && pwd)/work/gcloud-config"
export GOOGLE_GENAI_USE_VERTEXAI="true"

# Use this only when gcloud is installed inside the repository workspace.
export RELAY_GCLOUD_BIN="${RELAY_GCLOUD_BIN:-gcloud}"

mkdir -p "$CLOUDSDK_CONFIG"
"$RELAY_GCLOUD_BIN" config set project "$GOOGLE_CLOUD_PROJECT"
"$RELAY_GCLOUD_BIN" config set compute/region "$GOOGLE_CLOUD_REGION"

# Confirm both landed. `compute/region` unset is a common cause of a later
# deploy defaulting to the wrong region.
"$RELAY_GCLOUD_BIN" config get-value project
"$RELAY_GCLOUD_BIN" config get-value compute/region
```

For a different project, replace the values before running any create or enable command. Do not blindly reuse the Firestore region from another project.

## 3. Sign in and create ADC

Interactive login is for a developer workstation only. It opens a browser, so an agent can print the command and let the user complete the login.

```bash
"$RELAY_GCLOUD_BIN" auth login --update-adc --project="$GOOGLE_CLOUD_PROJECT"
```

Verify the active account and ADC without printing a token:

```bash
"$RELAY_GCLOUD_BIN" auth list
"$RELAY_GCLOUD_BIN" auth application-default print-access-token >/dev/null
```

Never run interactive login in CI, a container image, or a deployed service. In those environments, use the attached runtime service account or workload identity.

### Optional: service-account impersonation

After a least-privilege development identity exists, use short-lived impersonated credentials instead of the bootstrap account:

```bash
export RELAY_AGENT_SA="relay-dev-agent@${GOOGLE_CLOUD_PROJECT}.iam.gserviceaccount.com"

"$RELAY_GCLOUD_BIN" config set auth/impersonate_service_account "$RELAY_AGENT_SA"
"$RELAY_GCLOUD_BIN" auth application-default login \
  --impersonate-service-account="$RELAY_AGENT_SA" \
  --project="$GOOGLE_CLOUD_PROJECT"
```

The human account needs `roles/iam.serviceAccountTokenCreator` on this service account. Grant it only to the named developer account, not to all users. Remove the impersonation setting when it is no longer needed:

```bash
"$RELAY_GCLOUD_BIN" config unset auth/impersonate_service_account
```

## 4. Enable the required APIs

Keep this list explicit and reviewed. Do not let application code enable APIs dynamically.

```bash
"$RELAY_GCLOUD_BIN" services enable \
  aiplatform.googleapis.com \
  run.googleapis.com \
  firestore.googleapis.com \
  pubsub.googleapis.com \
  secretmanager.googleapis.com \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com \
  gmail.googleapis.com \
  people.googleapis.com \
  calendar-json.googleapis.com \
  routes.googleapis.com \
  places-backend.googleapis.com \
  firebase.googleapis.com \
  identitytoolkit.googleapis.com \
  --project="$GOOGLE_CLOUD_PROJECT"
```

Review what is enabled:

```bash
"$RELAY_GCLOUD_BIN" services list \
  --enabled \
  --project="$GOOGLE_CLOUD_PROJECT" \
  --format="table(config.name)"
```

Enable only the services required by the current phase. A service can also require billing or additional IAM roles before its first API call succeeds.

## 5. Create or verify Firestore

Firestore Native has a permanent database location. Confirm the location with the project owner before creating the default database.

```bash
"$RELAY_GCLOUD_BIN" firestore databases list \
  --project="$GOOGLE_CLOUD_PROJECT"
```

If the project has no default database, create it once:

```bash
"$RELAY_GCLOUD_BIN" firestore databases create \
  --project="$GOOGLE_CLOUD_PROJECT" \
  --location="$GOOGLE_CLOUD_REGION" \
  --type=firestore-native
```

Describe the result:

```bash
"$RELAY_GCLOUD_BIN" firestore databases describe \
  --project="$GOOGLE_CLOUD_PROJECT" \
  --database="(default)"
```

For the current Relay setup, the approved Firestore and Cloud Run region is `asia-south1` (Mumbai). Vertex AI is called from `us-central1`.

## 6. Create service identities

Create separate identities for local development, API runtime, workers, and deployment. The exact IAM roles belong in the repository's infrastructure scripts and should be reviewed before applying them.

Example identity creation:

```bash
"$RELAY_GCLOUD_BIN" iam service-accounts create relay-dev-agent \
  --project="$GOOGLE_CLOUD_PROJECT" \
  --display-name="Relay development agent"
```

Typical Relay identities are:

- `relay-dev-agent` for supervised local diagnostics and development
- `relay-api` for the Cloud Run API
- `relay-worker` for asynchronous jobs
- `relay-deployer` for CI deployment

Grant resource-specific roles to these identities. Do not create or download service-account keys. If an existing tool asks for a JSON key, prefer changing the tool to support ADC or impersonation.

## 7. Store runtime secrets in Secret Manager

Create a secret without putting its value in source control:

```bash
"$RELAY_GCLOUD_BIN" secrets create RELAY_EXAMPLE_SECRET \
  --project="$GOOGLE_CLOUD_PROJECT" \
  --replication-policy=automatic
```

Add a value from an interactive prompt or a protected CI variable. The following pattern avoids putting the value in the command itself, but make sure the shell input is not logged:

```bash
read -r -s RELAY_SECRET_VALUE
printf '%s' "$RELAY_SECRET_VALUE" | \
  "$RELAY_GCLOUD_BIN" secrets versions add RELAY_EXAMPLE_SECRET \
  --project="$GOOGLE_CLOUD_PROJECT" \
  --data-file=-
unset RELAY_SECRET_VALUE
```

Give the runtime service account `roles/secretmanager.secretAccessor` only on the secrets it needs. See the [Secret Manager quickstart](https://cloud.google.com/secret-manager/docs/create-secret-quickstart) for details.

## 8. Verify access

These checks should be non-mutating:

```bash
"$RELAY_GCLOUD_BIN" projects describe "$GOOGLE_CLOUD_PROJECT"
"$RELAY_GCLOUD_BIN" billing projects describe "$GOOGLE_CLOUD_PROJECT"
"$RELAY_GCLOUD_BIN" auth application-default print-access-token >/dev/null
"$RELAY_GCLOUD_BIN" firestore databases describe \
  --project="$GOOGLE_CLOUD_PROJECT" \
  --database="(default)"
```

If the repository contains verification scripts, run them after reading them. Relay provides:

```bash
infra/gcp/bootstrap.sh \
  --project="$GOOGLE_CLOUD_PROJECT" \
  --region="$GOOGLE_CLOUD_REGION" \
  --dry-run

infra/gcp/verify-agent-access.sh
```

The first command should be reviewed before removing `--dry-run`. The second should report missing access without enabling APIs or printing credentials.

## 9. Local Firestore emulator

The emulator is useful for tests and does not require production Firestore access. Install the emulator component through the Cloud SDK, ensure a supported Java runtime is available, then start it on a local-only port:

```bash
# The emulator needs a Java runtime on PATH. If the workspace has a local JDK,
# point JAVA_HOME at it first.
export JAVA_HOME="$(cd .. && pwd)/work/temurin/jdk-21.0.12.1+1/Contents/Home"
export PATH="$JAVA_HOME/bin:$PATH"

"$RELAY_GCLOUD_BIN" components install cloud-firestore-emulator
"$RELAY_GCLOUD_BIN" emulators firestore start \
  --project="$GOOGLE_CLOUD_PROJECT" \
  --host-port="127.0.0.1:8080"
```

In a separate shell:

```bash
export FIRESTORE_EMULATOR_HOST="127.0.0.1:8080"
```

If the emulator cannot start, check `java -version`, whether port `8080` is already in use, and whether the agent workspace allows local network binding. Do not point tests at production just to avoid an emulator failure.

## 10. Common agent setup problems

### `gcloud: command not found`

Install the CLI or set `RELAY_GCLOUD_BIN` to the full path of the local executable. Keep that install under an ignored `work/` directory if the workspace is sandboxed.

### The config directory is not writable

Set `CLOUDSDK_CONFIG` to a writable, ignored path before running any auth command:

```bash
export CLOUDSDK_CONFIG="$(cd .. && pwd)/work/gcloud-config"
mkdir -p "$CLOUDSDK_CONFIG"
```

### Browser login or token exchange fails

The agent may not have browser or outbound network access. Ask the user to run the printed login command locally, or run the command in an approved environment. Never ask the user to paste the resulting access token.

### Permission denied

Identify the exact missing permission and grant the narrowest role on the narrowest resource. Do not solve a single missing permission by making the agent an Owner.

### Firestore location conflict

Stop. The database location is durable. Confirm the intended region with the project owner rather than creating a second database or trying to move the existing one.

## Handoff checklist

Before another coding agent starts implementation, record:

- [ ] Project ID and billing status verified
- [ ] Approved Cloud Run and Firestore region recorded
- [ ] Vertex AI model location recorded
- [ ] `gcloud auth list` shows the intended account
- [ ] ADC check succeeds without printing a token
- [ ] Required APIs enabled from a reviewed, explicit list
- [ ] Firestore database type and location verified
- [ ] Development service-account impersonation configured, if used
- [ ] Runtime identities and least-privilege roles documented
- [ ] `CLOUDSDK_CONFIG`, local CLI paths, and emulator files are ignored by Git
- [ ] No API keys, OAuth codes, access tokens, or JSON keys are in the repository
- [ ] A dry-run bootstrap and repository verification have been run

## Current Relay values

| Setting | Value |
| --- | --- |
| Project | `massive-dynamo-302008` |
| Cloud Run and Firestore | `asia-south1` |
| Vertex AI | `us-central1` |
| Local CLI path used during setup | `work/google-cloud-cli/` |
| Local gcloud config | `work/gcloud-config/` |
| Authentication | ADC, with optional service-account impersonation |

Useful official references:

- [Install the Google Cloud CLI](https://cloud.google.com/sdk/docs/install)
- [Provide ADC credentials](https://cloud.google.com/docs/authentication/provide-credentials-adc)
- [Firestore locations](https://cloud.google.com/firestore/docs/locations)
- [Secret Manager quickstart](https://cloud.google.com/secret-manager/docs/create-secret-quickstart)
