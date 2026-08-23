# Local development

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

Use `FIRESTORE_LOCATION=asia-south1` for Relay. Cloud Run services run in `asia-south1`; Vertex AI remains in `us-central1`. See [Google Cloud agent access](gcloud-agent-access.md) for bootstrap, impersonation, and verification steps.
