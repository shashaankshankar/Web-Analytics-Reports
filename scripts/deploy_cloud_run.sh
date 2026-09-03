#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "$script_dir/.." && pwd)"

cd "$repo_root"
"$script_dir/assert_main_branch.sh"

readonly PROJECT_ID="web-analytics-agency-prod"
readonly CALLBACK_SERVICE="measurement-oauth-callback"
readonly REPORT_SERVICE="client-growth-reports"
readonly CALLBACK_REGION="us-central1"
readonly REPORT_REGION="us-east1"
readonly CALLBACK_SERVICE_ACCOUNT="gbp-oauth-callback@${PROJECT_ID}.iam.gserviceaccount.com"
readonly REPORT_SERVICE_ACCOUNT="analytics-reporting-reader@${PROJECT_ID}.iam.gserviceaccount.com"
readonly DEFAULT_CALLBACK_URI="https://${CALLBACK_SERVICE}-ptlwmdunva-uc.a.run.app/oauth/google/callback"

usage() {
  cat >&2 <<'EOF'
Usage:
  scripts/deploy_cloud_run.sh callback
  scripts/deploy_cloud_run.sh reports [--with-gbp-credentials]
  scripts/deploy_cloud_run.sh reports --dry-run [--with-gbp-credentials]

The command is intentionally production-specific. It deploys only from the
exact origin/main commit and only to the configured production project.
EOF
}

fail() {
  printf 'Deployment blocked: %s\n' "$1" >&2
  exit 64
}

service_kind="${1:-}"
[[ -n "$service_kind" ]] || { usage; exit 64; }
shift

dry_run=false
with_gbp_credentials=false
while (($#)); do
  case "$1" in
    --dry-run)
      dry_run=true
      ;;
    --with-gbp-credentials)
      with_gbp_credentials=true
      ;;
    *)
      usage
      fail "unsupported option '$1'."
      ;;
  esac
  shift
done

[[ -n "$(command -v gcloud || true)" ]] || fail "gcloud is not installed."

configured_project="${GCP_PROJECT_ID:-$PROJECT_ID}"
[[ "$configured_project" == "$PROJECT_ID" ]] || \
  fail "the production project is fixed to '$PROJECT_ID'."

run_args=(
  run deploy
  --source="$repo_root"
  --project="$PROJECT_ID"
)

case "$service_kind" in
  callback)
    [[ "$with_gbp_credentials" == false ]] || \
      fail "--with-gbp-credentials applies only to the reports service."
    [[ "$dry_run" == true ]] || {
      : "${GBP_OAUTH_CLIENT_ID:?set GBP_OAUTH_CLIENT_ID before deploying the callback}"
      : "${GBP_OAUTH_EXPECTED_PLACE_ID:?set GBP_OAUTH_EXPECTED_PLACE_ID before deploying the callback}"
    }
    callback_uri="${GBP_OAUTH_REDIRECT_URI:-$DEFAULT_CALLBACK_URI}"
    run_args+=(
      "$CALLBACK_SERVICE"
      --region="$CALLBACK_REGION"
      --allow-unauthenticated
      --service-account="$CALLBACK_SERVICE_ACCOUNT"
      --command=uvicorn
      --args=app.oauth_main:app,--host=0.0.0.0,--port=8080,--no-access-log
      "--set-env-vars=GBP_OAUTH_CLIENT_ID=${GBP_OAUTH_CLIENT_ID:-<required>},GBP_OAUTH_REDIRECT_URI=${callback_uri},GBP_OAUTH_SECRET_ID=GBP_OAUTH_CREDENTIALS_JSON,GBP_OAUTH_EXPECTED_PLACE_ID=${GBP_OAUTH_EXPECTED_PLACE_ID:-<required>},GCP_PROJECT_ID=${PROJECT_ID}"
      --set-secrets=GBP_OAUTH_CLIENT_SECRET=GBP_OAUTH_CLIENT_SECRET:latest
    )
    ;;
  reports)
    run_args+=(
      "$REPORT_SERVICE"
      --region="$REPORT_REGION"
      --no-allow-unauthenticated
      --service-account="$REPORT_SERVICE_ACCOUNT"
      --set-env-vars=REPORT_DELIVERY_ENABLED=true,REPORT_ALLOWED_CLIENTS=thehouseofdental
      --set-secrets=OPENROUTER_API_KEY=OPENROUTER_API_KEY:latest,RESEND_API_KEY=RESEND_API_KEY:latest,RESEND_FROM_EMAIL=RESEND_FROM_EMAIL:latest
    )
    if [[ "$with_gbp_credentials" == true ]]; then
      [[ "$dry_run" == true ]] || {
        enabled_version="$(gcloud secrets versions list GBP_OAUTH_CREDENTIALS_JSON \
          --project="$PROJECT_ID" \
          --filter='state=ENABLED' \
          --limit=1 \
          --format='value(name)' 2>/dev/null || true)"
        [[ -n "$enabled_version" ]] || \
          fail "GBP_OAUTH_CREDENTIALS_JSON has no enabled version."
      }
      run_args+=(--set-secrets=GBP_OAUTH_CREDENTIALS_JSON=GBP_OAUTH_CREDENTIALS_JSON:latest)
    fi
    ;;
  *)
    usage
    fail "service must be 'callback' or 'reports'."
    ;;
esac

if [[ "$dry_run" == true ]]; then
  printf 'Deployment guard passed for %s from origin/main.\n' "$service_kind"
  exit 0
fi

exec gcloud "${run_args[@]}"
