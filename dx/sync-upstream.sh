#!/usr/bin/env bash
# dxoraxs fork: merge the newest MaxEllis release tag into main locally (used when the
# dx-sync-upstream workflow opened an issue). Resolve conflicts if any, run the tests, then:
#   git push origin main --follow-tags
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
git remote get-url upstream >/dev/null 2>&1 || git remote add upstream https://github.com/MaxEllis/orcaslicer-mcp.git
git checkout main
git pull --ff-only origin main
git fetch upstream 'refs/tags/v*:refs/tags/v*'
latest=$(git tag -l 'v[0-9]*' --sort=-v:refname | grep -v -- '-dx\.' | head -1)
if git merge-base --is-ancestor "$latest" HEAD; then
  echo "main already contains $latest"; exit 0
fi
echo "merging $latest"
git merge --no-edit "$latest"
uv run --extra dev pytest -q
