#!/usr/bin/env bash
# Update the Egg Inspector from GitHub and reinstall everything with the options of the last install:
# Python packages, the offline SLM (Ollama) and the desktop icon. Use this instead of a plain `git pull`.
#
#   bash ~/egg_inspector/deploy/update.sh
#
# Needs internet while it runs; the app itself then works offline.
set -euo pipefail

DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PKG_DIR="$(dirname "$DEPLOY_DIR")"
ARGS_FILE="$HOME/.local/share/egg_inspector/install-args"

git -C "$PKG_DIR" pull --ff-only
args="$(cat "$ARGS_FILE" 2>/dev/null || true)"
if [ -z "$args" ]; then
    echo "No earlier install found; installing webcam mode. Change it with: bash deploy/install.sh --backend ..." >&2
    args="--backend webcam"
fi
# shellcheck disable=SC2086  # the saved options are separate words on purpose
exec bash "$DEPLOY_DIR/install.sh" $args
