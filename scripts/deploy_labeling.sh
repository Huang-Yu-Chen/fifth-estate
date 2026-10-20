#!/usr/bin/env bash
# Deploy the blinded labeling site (src/fifth_estate/labeling) to Cloud Run, with a private Cloud
# Storage bucket for the blinded article text and the labels. Safe to re-run: it creates whatever is
# missing, then deploys a new revision.
#
#   bash scripts/deploy_labeling.sh
#
# Kept apart from the public API (fifth-estate-api, bucket <project>-serving): its own service and
# service account fifth-estate-labeling, which can touch only its own bucket <project>-labeling, and
# its own build account fifth-estate-labeling-build. Only the image repository (Artifact Registry
# "fifth-estate", set up with the public API) is shared.
set -euo pipefail

PROJECT="$(gcloud config get-value project 2>/dev/null)"
REGION="asia-east1"
SERVICE="fifth-estate-labeling"
BUCKET="${LABEL_BUCKET:-$PROJECT-labeling}"
SA="$SERVICE@$PROJECT.iam.gserviceaccount.com"
BUILD_SA="$SERVICE-build@$PROJECT.iam.gserviceaccount.com"
REPO="fifth-estate"
IMAGE="$REGION-docker.pkg.dev/$PROJECT/$REPO/labeling:$(date -u +%Y%m%d-%H%M%S)"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "Google Cloud project: $PROJECT (region $REGION)"
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
  storage.googleapis.com iam.googleapis.com --project "$PROJECT"

if ! gcloud storage buckets describe "gs://$BUCKET" --project "$PROJECT" >/dev/null 2>&1; then
  gcloud storage buckets create "gs://$BUCKET" --project "$PROJECT" --location "$REGION" \
    --uniform-bucket-level-access --public-access-prevention
  echo "✓ created private bucket gs://$BUCKET"
fi
if ! gcloud iam service-accounts describe "$SA" --project "$PROJECT" >/dev/null 2>&1; then
  gcloud iam service-accounts create "$SERVICE" --project "$PROJECT" --display-name "第五權 labeling site"
  echo "✓ created service account $SA"
fi
gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" --member "serviceAccount:$SA" \
  --role roles/storage.objectAdmin --quiet >/dev/null
if ! gcloud artifacts repositories describe "$REPO" --location "$REGION" --project "$PROJECT" >/dev/null 2>&1; then
  gcloud artifacts repositories create "$REPO" --repository-format docker --location "$REGION" --project "$PROJECT"
fi

# Cloud Build's default account (Compute Engine default) has no rights in this project, so the image
# is built by its own account: it may push to the image repository, read the uploaded source and
# write build logs, nothing else.
if ! gcloud iam service-accounts describe "$BUILD_SA" --project "$PROJECT" >/dev/null 2>&1; then
  gcloud iam service-accounts create "$SERVICE-build" --project "$PROJECT" --display-name "第五權 labeling site image builds"
  sleep 15  # let the new account propagate before granting it roles
fi
SOURCE_BUCKET="gs://${PROJECT}_cloudbuild"
gcloud storage buckets describe "$SOURCE_BUCKET" --project "$PROJECT" >/dev/null 2>&1 \
  || gcloud storage buckets create "$SOURCE_BUCKET" --project "$PROJECT" --location "$REGION" --uniform-bucket-level-access
gcloud storage buckets add-iam-policy-binding "$SOURCE_BUCKET" --member "serviceAccount:$BUILD_SA" \
  --role roles/storage.objectViewer --quiet >/dev/null
gcloud artifacts repositories add-iam-policy-binding "$REPO" --location "$REGION" --project "$PROJECT" \
  --member "serviceAccount:$BUILD_SA" --role roles/artifactregistry.writer --quiet >/dev/null
gcloud projects add-iam-policy-binding "$PROJECT" --member "serviceAccount:$BUILD_SA" \
  --role roles/logging.logWriter --condition None --quiet >/dev/null

# Build context: only what the server needs.
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
cp "$ROOT/deploy/labeling/Dockerfile" "$ROOT/pyproject.toml" "$ROOT/uv.lock" "$ROOT/README.md" "$STAGE/"
mkdir -p "$STAGE/fifth_estate"
cp "$ROOT/src/fifth_estate/__init__.py" "$STAGE/fifth_estate/"
rsync -a --exclude __pycache__ "$ROOT/src/fifth_estate/labeling" "$STAGE/fifth_estate/"
cat > "$STAGE/cloudbuild.yaml" <<EOF
steps:
  - name: gcr.io/cloud-builders/docker
    args: ["build", "-t", "$IMAGE", "."]
images: ["$IMAGE"]
serviceAccount: projects/$PROJECT/serviceAccounts/$BUILD_SA
options:
  logging: CLOUD_LOGGING_ONLY
EOF

gcloud builds submit "$STAGE" --config "$STAGE/cloudbuild.yaml" --project "$PROJECT" --quiet
# max-instances 1: which article is open on whose screen is kept in memory.
gcloud run deploy "$SERVICE" --image "$IMAGE" --project "$PROJECT" --region "$REGION" \
  --service-account "$SA" --allow-unauthenticated \
  --min-instances 0 --max-instances 1 --concurrency 40 --cpu 1 --memory 512Mi --timeout 60 \
  --set-env-vars "FIFTH_LABEL_STORE=gs://$BUCKET" --quiet

URL="$(gcloud run services describe "$SERVICE" --project "$PROJECT" --region "$REGION" --format 'value(status.url)')"

# The CLI (publish, invite, status, sync) finds the site through .env (gitignored).
ENV_FILE="$ROOT/.env"
upsert() {
  touch "$ENV_FILE"
  local tmp
  tmp="$(mktemp)"
  { grep -v "^$1=" "$ENV_FILE" || true; echo "$1=$2"; } > "$tmp"
  cat "$tmp" > "$ENV_FILE"
  rm -f "$tmp"
}
upsert FIFTH_LABEL_STORE "gs://$BUCKET"
upsert FIFTH_LABEL_URL "$URL"

echo "✓ labeling site: $URL (FIFTH_LABEL_STORE and FIFTH_LABEL_URL saved in .env)"
echo "Next:"
echo "  uv run fifth labeling publish --set gold --coders 2"
echo "  uv run fifth labeling invite --id <id> --name <name>"
