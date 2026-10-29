#!/usr/bin/env bash
# Install the Egg Inspector as a Raspberry Pi OS desktop app (icon on the desktop + in the menu).
#
#   bash deploy/install.sh                 # mock backend (fake images, no camera/model needed)
#   bash deploy/install.sh --backend pi    # real camera, GPIO, UV-C relay and model
#   bash deploy/install.sh --backend pi --camera /dev/videoN   # the station with a USB webcam instead of the ribbon camera
#   bash deploy/install.sh --backend webcam [--camera /dev/videoN]   # USB webcam + Live test, no GPIO
#   bash deploy/install.sh --autostart     # also open the app automatically at login (kiosk)
#   bash deploy/install.sh --no-apt        # skip apt-get (offline / already installed)
#   bash deploy/install.sh --no-slm        # skip Ollama + the small language model (heatmap explanations)
#   bash deploy/install.sh --uninstall     # remove icons, autostart entry and venv
#
# Later updates: bash deploy/update.sh (git pull + this installer with the same options).
#
# Re-run it any time to change the options. The app runs from wherever this folder lives,
# so keep it somewhere permanent (e.g. ~/egg_inspector) before installing.
set -euo pipefail

ORIG_ARGS="$*"
BACKEND=mock
AUTOSTART=0
APT=1
UNINSTALL=0
CAMERA=
SLM=1
while [ $# -gt 0 ]; do
    case "$1" in
        --backend)   BACKEND="${2:-}"; shift 2 ;;
        --autostart) AUTOSTART=1; shift ;;
        --no-apt)    APT=0; shift ;;
        --uninstall) UNINSTALL=1; shift ;;
        --camera)    CAMERA="${2:-}"; shift 2 ;;
        --no-slm)    SLM=0; shift ;;
        -h|--help)   sed -n '2,18p' "$0"; exit 0 ;;
        *)           echo "Unknown option: $1 (try --help)" >&2; exit 2 ;;
    esac
done
case "$BACKEND" in mock|pi|webcam) ;; *) echo "--backend must be 'mock', 'pi' or 'webcam'" >&2; exit 2 ;; esac

DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PKG_DIR="$(dirname "$DEPLOY_DIR")"
APP_ID=egg-inspector
DATA="$HOME/.local/share/egg_inspector"
VENV="$DATA/venv"
ICON="$DATA/$APP_ID.svg"
DESKTOP_DIR="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"
MENU_FILE="$HOME/.local/share/applications/$APP_ID.desktop"
DESKTOP_FILE="$DESKTOP_DIR/$APP_ID.desktop"
AUTOSTART_FILE="$HOME/.config/autostart/$APP_ID.desktop"

say() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }

if [ "$UNINSTALL" = 1 ]; then
    rm -f "$MENU_FILE" "$DESKTOP_FILE" "$AUTOSTART_FILE"
    rm -rf "$DATA"
    say "Removed the Egg Inspector launcher, icon and venv (the source folder was left alone)."
    exit 0
fi

if [ "$(basename "$PKG_DIR")" != "egg_inspector" ]; then
    echo "The app folder must be named 'egg_inspector' (it is '$(basename "$PKG_DIR")')." >&2
    exit 1
fi

# Files edited on Windows may carry CRLF line endings, which break bash scripts.
sed -i 's/\r$//' "$DEPLOY_DIR"/*.sh
chmod +x "$DEPLOY_DIR/run.sh"

if [ "$APT" = 1 ]; then
    say "Installing system packages (needs sudo)"
    pkgs=(python3-venv python3-tk python3-pil python3-pil.imagetk python3-numpy fonts-dejavu-core zenity curl)
    [ "$BACKEND" = pi ] && pkgs+=(python3-picamera2 python3-gpiozero)
    [ "$BACKEND" != mock ] && pkgs+=(python3-opencv v4l-utils)      # USB webcam (Live test)
    sudo apt-get update
    sudo apt-get install -y "${pkgs[@]}"
    # UI font; optional, the app falls back to DejaVu Sans if it is unavailable.
    sudo apt-get install -y fonts-inter || echo "fonts-inter not available; using DejaVu Sans"
fi

say "Creating Python environment in $VENV"
mkdir -p "$DATA"
echo "$ORIG_ARGS" >"$DATA/install-args"         # update.sh reinstalls with the same options
python3 -m venv --system-site-packages "$VENV"
# /tmp is a small RAM disk on Raspberry Pi OS (Debian 13), too small to unpack PyTorch: unpack on the SD card.
export TMPDIR="$DATA/tmp"
mkdir -p "$TMPDIR"
PIP=("$VENV/bin/pip" install --quiet --disable-pip-version-check --no-cache-dir --timeout 60)
# Weak Wi-Fi drops big downloads part-way; newer pip (25.1+) can resume them.
if "$VENV/bin/pip" install --help 2>/dev/null | grep -- --resume-retries >/dev/null; then
    PIP+=(--resume-retries 20)
fi
# The CPU-only PyTorch build (~200 MB). Plain PyPI may pick a build with NVIDIA GPU libraries (several GB),
# which fills the SD card and is no use on a Pi.
"${PIP[@]}" torch torchvision --index-url https://download.pytorch.org/whl/cpu
"${PIP[@]}" -r "$DEPLOY_DIR/requirements.txt"
rm -rf "$TMPDIR"
"$VENV/bin/python" -c "import customtkinter, numpy, PIL.ImageTk, httpx, pydantic, torch, torchvision" \
    || { echo "Dependency check failed; see the messages above." >&2; exit 1; }

# The small language model that words the heatmap explanation runs in Ollama on this machine.
# Without it the app still works and explains the heatmap in fixed wording.
if [ "$BACKEND" != mock ] && [ "$SLM" = 1 ]; then
    # Pick the SLM size this Pi's memory can hold next to the crack model (~1.6 GB peak) and the desktop.
    MEM_MB="$(awk '/MemTotal/ {print int($2 / 1024)}' /proc/meminfo)"
    if [ "$MEM_MB" -ge 6000 ]; then SLM_MODEL=qwen2.5:1.5b; SLM_SIZE="about 1 GB"
    elif [ "$MEM_MB" -ge 3000 ]; then SLM_MODEL=qwen2.5:0.5b; SLM_SIZE="about 400 MB"
    else SLM_MODEL=; say "Only $MEM_MB MB of RAM: no SLM; explanations use the fixed wording (same facts)"
    fi
    if [ -n "$SLM_MODEL" ]; then
        if ! command -v ollama >/dev/null 2>&1; then
            say "Installing Ollama (runs the small language model on this Pi; needs internet this once)"
            curl -fsSL https://ollama.com/install.sh | sh \
                || say "Ollama install failed; explanations use fixed wording. Retry later or use --no-slm."
        fi
        if command -v ollama >/dev/null 2>&1; then
            say "Downloading the SLM $SLM_MODEL for $MEM_MB MB of RAM (once, $SLM_SIZE; offline after this)"
            ollama pull "$SLM_MODEL" \
                || say "Could not download $SLM_MODEL; explanations use fixed wording until: ollama pull $SLM_MODEL"
        fi
    fi
fi

ARGS="--backend $BACKEND"
if [ "$BACKEND" = webcam ]; then
    if [ -z "$CAMERA" ]; then         # first USB camera; on a Pi 5 the webcam often isn't /dev/video0
        CAMERA="$(v4l2-ctl --list-devices 2>/dev/null | awk '/usb/ {getline; print $1; exit}')"
    fi
    [ -n "$CAMERA" ] || { echo "No USB webcam found. Plug it in, or pass --camera /dev/videoN" >&2; exit 1; }
    ARGS="$ARGS --camera $CAMERA"
elif [ "$BACKEND" = pi ] && [ -n "$CAMERA" ]; then
    ARGS="$ARGS --camera $CAMERA"     # USB webcam on the station instead of the ribbon camera
fi

say "Installing launcher ($ARGS)"
cp "$DEPLOY_DIR/$APP_ID.svg" "$ICON"
entry="[Desktop Entry]
Type=Application
Version=1.0
Name=Egg Inspector
GenericName=Duck egg inspection + UV-C
Comment=Micro-fissure inspection with UV-C disinfection
Exec=\"$DEPLOY_DIR/run.sh\" $ARGS
Icon=$ICON
Terminal=false
Categories=Utility;
StartupNotify=false"

mkdir -p "$(dirname "$MENU_FILE")" "$DESKTOP_DIR"
for f in "$MENU_FILE" "$DESKTOP_FILE"; do
    printf '%s\n' "$entry" >"$f"
    chmod +x "$f"
done
# Mark the desktop icon as trusted so the file manager launches it without asking.
gio set "$DESKTOP_FILE" metadata::trusted true 2>/dev/null || true
command -v update-desktop-database >/dev/null && update-desktop-database -q "$(dirname "$MENU_FILE")" || true

if [ "$AUTOSTART" = 1 ]; then
    mkdir -p "$(dirname "$AUTOSTART_FILE")"
    printf '%s\n' "$entry" >"$AUTOSTART_FILE"
    say "Autostart enabled: the app opens at login"
else
    rm -f "$AUTOSTART_FILE"
fi

if [ "$BACKEND" != mock ] && [ ! -f "$PKG_DIR/condinst_model.pth" ]; then
    if [ -f "$PKG_DIR/model_best.pth" ] || [ -f "$PKG_DIR/model_best_quantized.pth" ]; then
        say "Using the Mask R-CNN model. For the CondInst model, copy condinst_model.pth into $PKG_DIR (see README)"
    else
        say "Model weights missing: $PKG_DIR/model_best_quantized.pth (it comes with git pull; see README)"
    fi
fi

say "Checking that everything runs on this Pi without internet"
(cd "$(dirname "$PKG_DIR")" && "$VENV/bin/python" -m "$(basename "$PKG_DIR").selfcheck") \
    || say "Self-check found a problem (see above); fix it while you still have internet."

say "Done. Double-tap 'Egg Inspector' on the desktop (also in Menu > Accessories)."
say "Logs: ~/.local/state/egg_inspector/app.log"
