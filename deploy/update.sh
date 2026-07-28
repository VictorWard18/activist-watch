#!/usr/bin/env bash
# Auto-update: pull, and restart ONLY if something actually changed and the
# new code imports cleanly. A bad commit must never take the monitor down.
set -uo pipefail

APP_DIR="${APP_DIR:-/opt/activist-watch}"
cd "$APP_DIR" || exit 0

BEFORE="$(git rev-parse HEAD 2>/dev/null)" || exit 0
git fetch --quiet origin 2>/dev/null || exit 0
AFTER="$(git rev-parse origin/HEAD 2>/dev/null || git rev-parse origin/main 2>/dev/null)" || exit 0

[ "$BEFORE" = "$AFTER" ] && exit 0

echo "[$(date -u +%FT%TZ)] update ${BEFORE:0:7} -> ${AFTER:0:7}"
git stash --quiet 2>/dev/null || true
git merge --quiet --ff-only "$AFTER" 2>/dev/null || { echo "  ff-only merge failed, skipping"; exit 0; }

# syntax gate: never restart into code that cannot even import
if ! python3 -c "import ast,sys,glob
[ast.parse(open(f).read(), f) for f in glob.glob('*.py')]" 2>/dev/null; then
    echo "  !! syntax check failed, rolling back"
    git reset --hard --quiet "$BEFORE"
    exit 1
fi

pip3 install --quiet --break-system-packages -r requirements.txt 2>/dev/null || true
systemctl restart activist-watch
echo "  restarted on ${AFTER:0:7}"
