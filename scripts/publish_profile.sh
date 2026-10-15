#!/usr/bin/env bash
# Publish the already-rendered README through a reviewable PR, never the default ref.
set -euo pipefail
: "${GH_TOKEN:?GITHUB_TOKEN must be supplied by the workflow}"
: "${DEFAULT_BRANCH:?}"
: "${RUNNER_TEMP:?}"
if [[ "${GITHUB_REPOSITORY:-}" != ogarciarevett/ogarciarevett ||
      "${GITHUB_REF:-}" != "refs/heads/$DEFAULT_BRANCH" ]]; then
  echo 'Publication requires this repository and its actual default ref.' >&2
  exit 1
fi
case "${GITHUB_EVENT_NAME:-}" in
  schedule|workflow_dispatch) ;;
  *) echo 'Publication requires a trusted scheduled or manual default-branch run.' >&2; exit 1 ;;
esac
if [[ ! -f README.md || -L README.md ]]; then
  echo 'README.md must be a regular file.' >&2
  exit 1
fi
UPDATE_BRANCH=automation/profile-contributions
if [[ "$DEFAULT_BRANCH" == "$UPDATE_BRANCH" ]]; then
  echo 'The update branch must not be the default branch.' >&2
  exit 1
fi
git diff --cached --quiet
git diff --quiet -- . ':!README.md'
if git diff --quiet -- README.md; then
  exit 0
fi
GENERATED=$(mktemp "$RUNNER_TEMP/profile-readme.XXXXXX")
trap 'rm -f "$GENERATED"' EXIT
cp README.md "$GENERATED"
git restore -- README.md
BASE=$(git rev-parse HEAD)
git config user.name 'github-actions[bot]'
git config user.email '41898282+github-actions[bot]@users.noreply.github.com'
if git ls-remote --exit-code --heads origin "refs/heads/$UPDATE_BRANCH" >/dev/null; then
  git fetch origin "refs/heads/$UPDATE_BRANCH:refs/remotes/origin/$UPDATE_BRANCH"
  if [[ -n "$(git diff --name-only "$BASE...origin/$UPDATE_BRANCH" -- . ':!README.md')" ]]; then
    echo 'The automation branch contains changes outside README.md; human review required.' >&2
    exit 1
  fi
  README_MODE=$(git ls-tree "origin/$UPDATE_BRANCH" -- README.md | cut -d' ' -f1)
  if [[ "$README_MODE" != 100644 ]]; then
    echo 'The automation README must be a regular non-executable blob.' >&2
    exit 1
  fi
  git switch -c "$UPDATE_BRANCH" "origin/$UPDATE_BRANCH"
  git merge --no-edit "$BASE"
else
  status=$?
  [[ "$status" == 2 ]] || exit "$status"
  git switch -c "$UPDATE_BRANCH"
fi
cp "$GENERATED" README.md
if ! git diff --quiet -- README.md; then
  curl --fail --silent --show-error --location \
    https://github.com/gitleaks/gitleaks/releases/download/v8.30.1/gitleaks_8.30.1_linux_x64.tar.gz \
    --output "$RUNNER_TEMP/gitleaks.tar.gz"
  echo "551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb  $RUNNER_TEMP/gitleaks.tar.gz" | sha256sum --check
  tar -xzf "$RUNNER_TEMP/gitleaks.tar.gz" -C "$RUNNER_TEMP" gitleaks
  git add -- README.md
  "$RUNNER_TEMP/gitleaks" git --pre-commit --staged --redact --no-banner
  git commit -m "docs: refresh latest contributions"
fi
# Non-forced publication preserves prior topic commits and fails on concurrent drift.
git push origin "HEAD:refs/heads/$UPDATE_BRANCH"
PR=$(gh api --method GET "repos/$GITHUB_REPOSITORY/pulls" \
  -f state=open -f "head=ogarciarevett:$UPDATE_BRANCH" -f "base=$DEFAULT_BRANCH" \
  --jq 'if length > 1 then error("Ambiguous automation PRs") else .[0].number // empty end')
if [[ -z "$PR" ]]; then
  gh pr create --repo "$GITHUB_REPOSITORY" --base "$DEFAULT_BRANCH" \
    --head "$UPDATE_BRANCH" --title 'docs: refresh latest contributions' \
    --body 'Automated refresh of the existing public contribution feed. The workflow runs renderer tests before publication and requires a staged secret scan for changed commits. Human review is required before merge; this workflow never approves or merges PRs.'
fi
