#!/usr/bin/env bash
# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
#
# FE-EXC — Build the public (open-source) mirror branch.
#
# The public source-available release must NOT contain our commercial/cloud
# deployment artifacts (Cloudflare Worker, wrangler config, private CI/CD, env
# files) nor our internal docs/ (dev change logs, design specs, planning notes,
# backend contracts — docs/licenses is the sole exception). This script derives a
# clean `public` branch from the CURRENT branch and strips those, leaving the dev
# branch fully deployable.
#
# Run it on every release you want to publish — it is idempotent: it recreates
# the public branch from scratch each time, so re-running re-syncs the mirror
# with whatever the source branch currently holds.
#
# Usage:
#   scripts/build-public-mirror.sh [public-branch-name]   # default: public
#
# Then review and push deliberately:
#   git log --stat public        # inspect what the mirror contains
#   git push <public-remote> public:main
#
# This script DOES create a local commit on the mirror branch, but never pushes.

set -euo pipefail

PUBLIC_BRANCH="${1:-public}"
SRC_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

# --- Private deployment / cloud artifacts to remove from the public mirror. ---
# These are commercial/operational and some may carry secrets. Keep them on the
# dev branch (needed to deploy); exclude them from anything published.
PRIVATE_PATHS=(
  "worker"                              # Cloudflare Worker (proxy + CSP/security headers)
  "wrangler.jsonc"                      # Cloudflare deploy config (account/env wiring)
  ".github/workflows/deploy.yml"        # private release pipeline
  ".github/workflows/image.yml"         # private image pipeline
  "DEPLOY.md"                           # operator runbook (private infra, hostnames)
  ".env.ce"                             # internal CE build config
  "src/__tests__/worker"               # tests for the removed worker
)
# NOTE: .env.example is intentionally KEPT — it contains no secrets and is the
# standard onboarding template for self-hosters. Add it above if you'd rather
# strip every .env* file. Any real secret-bearing .env is git-ignored and never
# tracked, so it cannot reach the mirror regardless.

if [ -n "$(git status --porcelain)" ]; then
  echo "ERROR: working tree is dirty. Commit or stash first so the mirror is reproducible." >&2
  exit 1
fi

echo "Building public mirror '$PUBLIC_BRANCH' from '$SRC_BRANCH'..."

# Recreate the mirror branch from the source HEAD.
git branch -f "$PUBLIC_BRANCH" "$SRC_BRANCH"
git switch "$PUBLIC_BRANCH"

# Remove private paths (ignore ones that don't exist).
for p in "${PRIVATE_PATHS[@]}"; do
  if git ls-files --error-unmatch "$p" >/dev/null 2>&1 || [ -e "$p" ]; then
    git rm -r --quiet --ignore-unmatch "$p"
  fi
done

# Strip internal docs (dev change logs, design specs, planning notes, backend
# contracts) — they add no value for external consumers and leak internal
# architecture/backend details. Keep ONLY docs/licenses (the OSS compliance
# report, DEP-03). Stripping the whole dir means any future docs/* is excluded
# by default; only the license report is deliberately re-added.
git rm -r --quiet --ignore-unmatch docs
git checkout "$SRC_BRANCH" -- docs/licenses
git add docs/licenses

# Drop wrangler/worker tooling from package.json (the mirror can't deploy via CF).
node - <<'NODE'
const fs = require("fs");
const p = JSON.parse(fs.readFileSync("package.json", "utf8"));
for (const s of ["build:worker", "deploy", "deploy:dry"]) delete p.scripts?.[s];
for (const d of ["wrangler", "@cloudflare/workers-types"]) delete p.devDependencies?.[d];
fs.writeFileSync("package.json", JSON.stringify(p, null, 2) + "\n");
NODE
git add package.json

git commit --quiet -m "chore(public): strip private deployment artifacts for OSS mirror"

echo
echo "Public mirror '$PUBLIC_BRANCH' built. Remaining tracked private files (should be none):"
git ls-files | grep -iE '^worker/|wrangler|deploy\.yml|image\.yml|^\.env\.(ce|local)|DEPLOY\.md|__tests__/worker' || echo "  (none) OK"
echo
echo "Internal docs stripped — remaining docs/ files (should be only docs/licenses):"
git ls-files 'docs/**' || echo "  (none)"
echo
echo "Note: the public build has no Cloudflare Worker, so it no longer stamps CSP/"
echo "security headers — a self-hoster must supply their own serving layer/headers."
echo
echo "Switched to '$PUBLIC_BRANCH'. Return with: git switch $SRC_BRANCH"
