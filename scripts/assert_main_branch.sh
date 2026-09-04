#!/usr/bin/env bash
set -euo pipefail

readonly MAIN_BRANCH="main"

fail() {
  printf 'Deployment blocked: %s\n' "$1" >&2
  exit 64
}

# Hosted builders do not necessarily include a .git directory. Use the
# provider-supplied immutable ref in those environments instead of guessing
# from the checkout directory.
if [[ -n "${BUILD_ID:-}" ]]; then
  [[ "${BRANCH_NAME:-}" == "$MAIN_BRANCH" ]] || \
    fail "Cloud Build must run from branch '$MAIN_BRANCH'."
  [[ -z "${TAG_NAME:-}" ]] || \
    fail "tag builds are not deployable; use the '$MAIN_BRANCH' branch trigger."
  exit 0
fi

if [[ "${GITHUB_ACTIONS:-}" == "true" ]]; then
  [[ "${GITHUB_REF:-}" == "refs/heads/$MAIN_BRANCH" ]] || \
    fail "GitHub Actions must run from refs/heads/$MAIN_BRANCH."
  exit 0
fi

repo_root="$(git rev-parse --show-toplevel 2>/dev/null || true)"
[[ -n "$repo_root" ]] || fail "the deployment must run from a Git checkout."
cd "$repo_root"

branch="$(git symbolic-ref --quiet --short HEAD 2>/dev/null || true)"
[[ "$branch" == "$MAIN_BRANCH" ]] || \
  fail "the checked-out branch is '$branch'; only '$MAIN_BRANCH' is deployable."

[[ -z "$(git status --porcelain=v1)" ]] || \
  fail "the '$MAIN_BRANCH' worktree must be clean."

origin_main="$(git rev-parse --verify "refs/remotes/origin/$MAIN_BRANCH" 2>/dev/null || true)"
[[ -n "$origin_main" ]] || \
  fail "origin/$MAIN_BRANCH is unavailable; fetch it before deploying."

head="$(git rev-parse HEAD)"
[[ "$head" == "$origin_main" ]] || \
  fail "HEAD is not the exact commit published as origin/$MAIN_BRANCH."
