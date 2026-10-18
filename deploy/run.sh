#!/usr/bin/env bash
# Launcher behind the desktop icon. Extra arguments are passed to `python -m egg_inspector`.
# Output goes to ~/.local/state/egg_inspector/app.log; a crash shows an error dialog
# because a desktop launch has no terminal to print to.
set -u

PKG_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${EGG_VENV:-$HOME/.local/share/egg_inspector/venv}"
STATE="$HOME/.local/state/egg_inspector"
LOG="$STATE/app.log"
mkdir -p "$STATE"

# Only one copy at a time (a double-tap on the icon would otherwise fight over GPIO and the camera).
exec 9>"$STATE/lock"
flock -n 9 || exit 0

# Keep the log small: roll it over at 1 MB.
if [ -f "$LOG" ] && [ "$(stat -c %s "$LOG")" -gt 1048576 ]; then
    mv -f "$LOG" "$LOG.1"
fi

echo "=== $(date '+%F %T') start: $*" >>"$LOG"
cd "$(dirname "$PKG_DIR")"
"$VENV/bin/python" -m "$(basename "$PKG_DIR")" "$@" >>"$LOG" 2>&1
status=$?
echo "=== $(date '+%F %T') exit $status" >>"$LOG"

if [ "$status" -ne 0 ] && command -v zenity >/dev/null 2>&1; then
    zenity --error --no-markup --width=520 --title="Egg Inspector" \
        --text="$(printf 'Egg Inspector stopped with an error (exit %s).\n\nLog: %s\n\n%s' \
                  "$status" "$LOG" "$(tail -n 8 "$LOG")")" >/dev/null 2>&1
fi
exit "$status"
