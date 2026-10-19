#!/usr/bin/env bash
# The website's backend on Google Cloud (src/fifth_estate/api). Safe to re-run: it creates what is missing
# and re-applies settings and permissions.
#
#   gs://$PROJECT-serving       versioned bucket; publish uploads serving.sqlite.gz there and older
#                               generations are kept for a week (roll back by copying one back)
#   Cloud Run fifth-estate-api  public, read-only, asia-east1; reads the bucket as service account fifth-estate-api
#   Artifact Registry fifth-estate (asia-east1)  container images (also used by the labeling site)
#   Secret fifth-estate-visitor-salt  key for the anonymous daily visitor hash (api/hits.py); never printed
#   Workload Identity Federation  GitHub Actions on main of this repo act as fifth-estate-publisher (uploads
#                               snapshots, publish.yml) and fifth-estate-deployer (deploys the API, api.yml);
#                               no keys are stored anywhere
#
#   bash scripts/setup_backend.sh            set up; deploy the API only if the service does not exist yet
#   bash scripts/setup_backend.sh --deploy   also build and deploy this checkout's API (normally api.yml does)
set -euo pipefail
cd "$(dirname "$0")/.."

PROJECT="$(gcloud config get-value project 2>/dev/null)"
NUMBER="$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')"
REGION=asia-east1
REPO=Huang-Yu-Chen/fifth-estate
REPO_ID="$(gh api "repos/$REPO" --jq .id)"  # the numeric id survives renames; the name alone could be re-registered
BUCKET="$PROJECT-serving"
SERVICE=fifth-estate-api
AR=fifth-estate
IMAGE="$REGION-docker.pkg.dev/$PROJECT/$AR/api"
POOL=github
PROVIDER=fifth-estate-repo
SECRET=fifth-estate-visitor-salt
SA_API="fifth-estate-api@$PROJECT.iam.gserviceaccount.com"
SA_PUB="fifth-estate-publisher@$PROJECT.iam.gserviceaccount.com"
SA_DEP="fifth-estate-deployer@$PROJECT.iam.gserviceaccount.com"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "Google Cloud project $PROJECT ($NUMBER), region $REGION, GitHub repo $REPO ($REPO_ID)"
gcloud services enable run.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com \
  secretmanager.googleapis.com iam.googleapis.com iamcredentials.googleapis.com sts.googleapis.com \
  storage.googleapis.com --project "$PROJECT"

echo "· bucket gs://$BUCKET"
if ! gcloud storage buckets describe "gs://$BUCKET" --project "$PROJECT" >/dev/null 2>&1; then
  gcloud storage buckets create "gs://$BUCKET" --project "$PROJECT" --location "$REGION" \
    --uniform-bucket-level-access --public-access-prevention --soft-delete-duration 0
fi
cat >"$TMP/lifecycle.json" <<'EOF'
{"rule": [
  {"action": {"type": "Delete"}, "condition": {"isLive": false, "daysSinceNoncurrentTime": 7}},
  {"action": {"type": "Delete"}, "condition": {"isLive": false, "numNewerVersions": 60}}
]}
EOF
gcloud storage buckets update "gs://$BUCKET" --project "$PROJECT" --versioning --lifecycle-file "$TMP/lifecycle.json" >/dev/null

echo "· service accounts"
for spec in "fifth-estate-api|Website API (reads snapshots)" "fifth-estate-publisher|Uploads snapshots (GitHub publish)" \
  "fifth-estate-deployer|Deploys the website API (GitHub)"; do
  name="${spec%%|*}"
  if ! gcloud iam service-accounts describe "$name@$PROJECT.iam.gserviceaccount.com" --project "$PROJECT" >/dev/null 2>&1; then
    gcloud iam service-accounts create "$name" --project "$PROJECT" --display-name "${spec#*|}"
  fi
done
gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" --project "$PROJECT" \
  --member "serviceAccount:$SA_API" --role roles/storage.objectViewer >/dev/null
gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" --project "$PROJECT" \
  --member "serviceAccount:$SA_PUB" --role roles/storage.objectUser >/dev/null

echo "· secret $SECRET"
if ! gcloud secrets describe "$SECRET" --project "$PROJECT" >/dev/null 2>&1; then
  head -c 32 /dev/urandom | base64 | tr -d '\n' |
    gcloud secrets create "$SECRET" --project "$PROJECT" --replication-policy automatic --data-file=- >/dev/null
fi
gcloud secrets add-iam-policy-binding "$SECRET" --project "$PROJECT" \
  --member "serviceAccount:$SA_API" --role roles/secretmanager.secretAccessor >/dev/null

echo "· Artifact Registry $AR"
if ! gcloud artifacts repositories describe "$AR" --location "$REGION" --project "$PROJECT" >/dev/null 2>&1; then
  gcloud artifacts repositories create "$AR" --location "$REGION" --project "$PROJECT" \
    --repository-format docker --description "第五權 container images"
fi
cat >"$TMP/cleanup.json" <<'EOF'
[{"name": "keep-recent", "action": {"type": "Keep"}, "mostRecentVersions": {"keepCount": 5}},
 {"name": "delete-old", "action": {"type": "Delete"}, "condition": {"olderThan": "30d"}}]
EOF
gcloud artifacts repositories set-cleanup-policies "$AR" --location "$REGION" --project "$PROJECT" \
  --policy "$TMP/cleanup.json" --no-dry-run >/dev/null
gcloud artifacts repositories add-iam-policy-binding "$AR" --location "$REGION" --project "$PROJECT" \
  --member "serviceAccount:$SA_DEP" --role roles/artifactregistry.writer >/dev/null
gcloud iam service-accounts add-iam-policy-binding "$SA_API" --project "$PROJECT" \
  --member "serviceAccount:$SA_DEP" --role roles/iam.serviceAccountUser >/dev/null

echo "· Workload Identity Federation for GitHub Actions"
if ! gcloud iam workload-identity-pools describe "$POOL" --location global --project "$PROJECT" >/dev/null 2>&1; then
  gcloud iam workload-identity-pools create "$POOL" --location global --project "$PROJECT" --display-name "GitHub Actions"
fi
CONDITION="assertion.repository_id == '$REPO_ID' && assertion.ref == 'refs/heads/main'"
MAPPING="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.repository_id=assertion.repository_id,attribute.ref=assertion.ref"
if gcloud iam workload-identity-pools providers describe "$PROVIDER" --workload-identity-pool "$POOL" --location global --project "$PROJECT" >/dev/null 2>&1; then
  gcloud iam workload-identity-pools providers update-oidc "$PROVIDER" --workload-identity-pool "$POOL" --location global \
    --project "$PROJECT" --attribute-mapping "$MAPPING" --attribute-condition "$CONDITION" >/dev/null
else
  gcloud iam workload-identity-pools providers create-oidc "$PROVIDER" --workload-identity-pool "$POOL" --location global \
    --project "$PROJECT" --display-name "fifth-estate repo, main" --issuer-uri "https://token.actions.githubusercontent.com" \
    --attribute-mapping "$MAPPING" --attribute-condition "$CONDITION"
fi
MEMBER="principalSet://iam.googleapis.com/projects/$NUMBER/locations/global/workloadIdentityPools/$POOL/attribute.repository_id/$REPO_ID"
for sa in "$SA_PUB" "$SA_DEP"; do
  gcloud iam service-accounts add-iam-policy-binding "$sa" --project "$PROJECT" \
    --member "$MEMBER" --role roles/iam.workloadIdentityUser >/dev/null
done

if [ "${1:-}" = "--deploy" ] || ! gcloud run services describe "$SERVICE" --region "$REGION" --project "$PROJECT" >/dev/null 2>&1; then
  TAG="manual-$(date -u +%Y%m%dT%H%M%SZ)"
  echo "· building $IMAGE:$TAG with Cloud Build (uploads only what .gcloudignore lets through)"
  # Built as the deployer (it may push images anyway) rather than the project's default compute account,
  # which new projects no longer give broad rights. It reads the uploaded source and writes build logs.
  gcloud storage buckets describe "gs://${PROJECT}_cloudbuild" --project "$PROJECT" >/dev/null 2>&1 ||
    gcloud storage buckets create "gs://${PROJECT}_cloudbuild" --project "$PROJECT" --location US --uniform-bucket-level-access
  gcloud storage buckets add-iam-policy-binding "gs://${PROJECT}_cloudbuild" --project "$PROJECT" \
    --member "serviceAccount:$SA_DEP" --role roles/storage.objectViewer >/dev/null
  gcloud projects add-iam-policy-binding "$PROJECT" --member "serviceAccount:$SA_DEP" --role roles/logging.logWriter \
    --condition None >/dev/null
  cat >"$TMP/cloudbuild.yaml" <<EOF
steps:
  - name: gcr.io/cloud-builders/docker
    args: ["build", "-t", "$IMAGE:$TAG", "."]
images: ["$IMAGE:$TAG"]
serviceAccount: projects/$PROJECT/serviceAccounts/$SA_DEP
options:
  logging: CLOUD_LOGGING_ONLY
EOF
  gcloud builds submit --project "$PROJECT" --region "$REGION" --config "$TMP/cloudbuild.yaml" .
  echo "· deploying $SERVICE"
  gcloud run deploy "$SERVICE" --project "$PROJECT" --region "$REGION" --image "$IMAGE:$TAG" \
    --service-account "$SA_API" --allow-unauthenticated \
    --set-env-vars "FIFTH_SERVING_BUCKET=$BUCKET" --set-secrets "FIFTH_VISITOR_SECRET=$SECRET:latest" \
    --cpu 1 --memory 1Gi --cpu-boost --concurrency 80 --min-instances 0 --max-instances 4 --timeout 60 --quiet
fi
gcloud run services add-iam-policy-binding "$SERVICE" --region "$REGION" --project "$PROJECT" \
  --member "serviceAccount:$SA_DEP" --role roles/run.developer >/dev/null

# The API serves nothing until a snapshot exists; the next publish uploads one, or this local one does now.
if ! gcloud storage objects describe "gs://$BUCKET/serving.sqlite.gz" >/dev/null 2>&1 && [ -f build/serving/serving.sqlite.gz ]; then
  echo "· uploading the local snapshot (build/serving/serving.sqlite.gz) so the API has data before the next publish"
  gcloud storage cp build/serving/serving.sqlite.gz "gs://$BUCKET/serving.sqlite.gz"
fi

URL="https://$SERVICE-$NUMBER.$REGION.run.app"
echo
echo "API:        $URL  (docs: $URL/docs, health: $URL/v1/health)"
echo "Workflows:  workload_identity_provider: projects/$NUMBER/locations/global/workloadIdentityPools/$POOL/providers/$PROVIDER"
echo "            publish.yml as $SA_PUB, api.yml as $SA_DEP"
echo "Dashboard:  web/.env.production VITE_API_BASE=$URL"
