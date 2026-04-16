#!/usr/bin/env bash
#
# radar64_sync.sh — Sync 64-track radar patch onto latest origin/carrot-wip-ui
#
# Usage: ./tools/radar64_sync.sh [--push] [--deploy]
#   --push    Push to github and myserver after successful sync
#   --deploy  Also pull on comma device (10.21.1.66)
#
# The script:
#   1. Fetches latest origin/carrot-wip-ui
#   2. Resets carrot-wip-ui-radar to origin/carrot-wip-ui
#   3. Cherry-picks the radar64 patch commit
#   4. Optionally pushes and deploys
#
# On conflict: stops and prints instructions. Ask Claude to resolve.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_DIR"

BRANCH="carrot-wip-ui-radar"
UPSTREAM="origin/carrot-wip-ui"
DEVICE_IP="10.21.1.66"
DEVICE_USER="comma"
SSH_KEY="$HOME/.ssh/github_id_rsa"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "${GREEN}[radar64]${NC} $*"; }
warn() { echo -e "${YELLOW}[radar64]${NC} $*"; }
err()  { echo -e "${RED}[radar64]${NC} $*" >&2; }

# Parse args
DO_PUSH=false
DO_DEPLOY=false
for arg in "$@"; do
  case "$arg" in
    --push)   DO_PUSH=true ;;
    --deploy) DO_DEPLOY=true; DO_PUSH=true ;;
    -h|--help)
      echo "Usage: $0 [--push] [--deploy]"
      echo "  --push    Push to github/myserver"
      echo "  --deploy  Push + pull on device"
      exit 0
      ;;
  esac
done

# Ensure we're on the right branch
CURRENT=$(git branch --show-current)
if [ "$CURRENT" != "$BRANCH" ]; then
  err "Not on $BRANCH (current: $CURRENT). Switch first: git checkout $BRANCH"
  exit 1
fi

# Save the radar patch commit hash (top commit)
RADAR_COMMIT=$(git log --oneline -1 --format="%H")
RADAR_MSG=$(git log --oneline -1 --format="%s")
log "Radar patch commit: ${RADAR_COMMIT:0:10} ($RADAR_MSG)"

# Step 1: Fetch upstream
log "Fetching $UPSTREAM..."
git fetch origin

# Check if upstream has new commits
UPSTREAM_HEAD=$(git rev-parse "$UPSTREAM")
RADAR_PARENT=$(git rev-parse HEAD~1)

if [ "$UPSTREAM_HEAD" = "$RADAR_PARENT" ]; then
  log "Already up to date. No sync needed."
  exit 0
fi

log "Upstream moved: $(git log --oneline "$RADAR_PARENT".."$UPSTREAM_HEAD" | wc -l) new commits"

# Step 2: Reset to upstream
log "Resetting $BRANCH to $UPSTREAM..."
git reset --hard "$UPSTREAM"

# Step 3: Cherry-pick radar patch
log "Cherry-picking radar64 patch ($RADAR_COMMIT)..."
if git cherry-pick "$RADAR_COMMIT" --no-edit; then
  log "Cherry-pick succeeded!"
else
  err "Cherry-pick CONFLICT detected!"
  err ""
  err "Conflicting files:"
  git diff --name-only --diff-filter=U
  err ""
  err "Options:"
  err "  1. Resolve manually, then: git cherry-pick --continue"
  err "  2. Abort: git cherry-pick --abort && git reset --hard $RADAR_COMMIT"
  err "  3. Ask Claude: '레이더트랙 64 싱크해줘'"
  exit 1
fi

# Step 4: Push
if $DO_PUSH; then
  log "Pushing to github..."
  git push github "$BRANCH" --force-with-lease
  log "Pushing to myserver..."
  git push myserver "$BRANCH" --force-with-lease
  log "Push complete."
fi

# Step 5: Deploy to device
if $DO_DEPLOY; then
  log "Deploying to device ($DEVICE_IP)..."
  SSH_CMD="ssh -i $SSH_KEY -o ConnectTimeout=5 -o StrictHostKeyChecking=no $DEVICE_USER@$DEVICE_IP"
  if $SSH_CMD "cd /data/openpilot && GIT_SSL_NO_VERIFY=1 git fetch relena && git reset --hard relena/$BRANCH && touch prebuilt"; then
    log "Device updated successfully."
  else
    warn "Device unreachable or deploy failed. Deploy manually when device is online."
  fi
fi

log "Sync complete!"
git log --oneline -3
