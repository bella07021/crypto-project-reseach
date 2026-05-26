#!/bin/bash
set -euo pipefail

PROJECT_DIR="/Users/apple/Documents/New project"
ENV_FILE="$PROJECT_DIR/.env.watcher"

cd "$PROJECT_DIR"
mkdir -p "$PROJECT_DIR/logs"

if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

export PATH="/Users/apple/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
export PYTHONUNBUFFERED=1

if [ -z "${GITHUB_TOKEN:-}" ]; then
  echo "GITHUB_TOKEN is not set. Fill $ENV_FILE, then run: launchctl kickstart -k gui/$(id -u)/com.bella.crypto-score-watcher"
  exec /bin/sleep 3600
fi

exec /usr/bin/python3 "$PROJECT_DIR/request_watcher.py" --interval "${WATCHER_INTERVAL_SECONDS:-10}"
